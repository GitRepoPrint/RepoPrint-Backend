import time

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.models import User
from app.services import audit, keycloak_admin

JWKS_REFRESH_SECONDS= 60
UNAUTHORIZED= {"WWW-Authenticate": "Bearer"}

bearer= HTTPBearer(auto_error=False)
_keys: dict[str, jwt.PyJWK]= {}
_fetched_at: float | None= None


async def fetch_jwks() -> dict:
    async with httpx.AsyncClient(timeout=5) as client:
        response= await client.get(settings.jwks_url)
        response.raise_for_status()
        return response.json()


async def refresh_keys() -> None:
    global _fetched_at
    jwks= await fetch_jwks()
    _keys.clear()
    _keys.update({key.key_id: key for key in jwt.PyJWKSet.from_dict(jwks).keys})
    _fetched_at= time.monotonic()


async def signing_key(kid: str | None) -> jwt.PyJWK | None:
    stale= _fetched_at is None or time.monotonic() - _fetched_at > JWKS_REFRESH_SECONDS
    if kid not in _keys and stale:
        await refresh_keys()
    return _keys.get(kid)


async def current_claims(credentials: HTTPAuthorizationCredentials | None= Depends(bearer)) -> dict:
    if credentials is None:
        raise HTTPException(401, "Sessão necessária", headers=UNAUTHORIZED)
    token= credentials.credentials
    try:
        kid= jwt.get_unverified_header(token).get("kid")
        key= await signing_key(kid)
        if key is None:
            raise jwt.InvalidTokenError("kid desconhecido")
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.keycloak_audience,
            issuer=settings.keycloak_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Sessão inválida ou expirada", headers=UNAUTHORIZED)
    except httpx.HTTPError:
        raise HTTPException(503, "Serviço de autenticação indisponível")


async def sync_profile(session: AsyncSession, user: User, claims: dict) -> None:
    email= claims.get("email") or user.email
    name= claims.get("name") or claims.get("preferred_username") or user.name
    if (email, name)== (user.email, user.name):
        return
    user.email= email
    user.name= name
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Já existe uma conta com este email")


async def get_current_user(claims: dict= Depends(current_claims), session: AsyncSession= Depends(get_session)) -> User:
    return await find_or_create_user(claims, session)


async def optional_user(
    credentials: HTTPAuthorizationCredentials | None= Depends(bearer), session: AsyncSession= Depends(get_session)
) -> User | None:
    if credentials is None:
        return None
    return await find_or_create_user(await current_claims(credentials), session)


async def find_or_create_user(claims: dict, session: AsyncSession) -> User:
    sub= claims["sub"]
    user= await session.scalar(select(User).where(User.keycloak_sub== sub))
    if user:
        await sync_profile(session, user, claims)
        return user
    email= claims.get("email")
    if not email:
        raise HTTPException(403, "A conta não tem email")
    if settings.keycloak_admin_client_secret is not None:
        try:
            still_exists= await keycloak_admin.user_exists(sub)
        except keycloak_admin.KeycloakUnavailable:
            raise HTTPException(503, "Serviço de autenticação indisponível")
        if not still_exists:
            raise HTTPException(401, "A conta foi apagada", headers=UNAUTHORIZED)
    values= {"keycloak_sub": sub, "email": email, "name": claims.get("name") or claims.get("preferred_username")}
    created= await session.scalar(insert(User).values(values).on_conflict_do_nothing().returning(User.id))
    if created:
        audit.record(session, "account.created", user_id=created)
    await session.commit()
    user= await session.scalar(select(User).where(User.keycloak_sub== sub))
    if user is None:
        raise HTTPException(409, "Já existe uma conta com este email")
    return user
