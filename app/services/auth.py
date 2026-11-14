import time

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.models import User

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


async def get_current_user(claims: dict= Depends(current_claims), session: AsyncSession= Depends(get_session)) -> User:
    sub= claims["sub"]
    user= await session.scalar(select(User).where(User.keycloak_sub== sub))
    if user:
        return user
    email= claims.get("email")
    if not email:
        raise HTTPException(403, "A conta não tem email")
    values= {"keycloak_sub": sub, "email": email, "name": claims.get("name") or claims.get("preferred_username")}
    await session.execute(insert(User).values(values).on_conflict_do_nothing())
    await session.commit()
    user= await session.scalar(select(User).where(User.keycloak_sub== sub))
    if user is None:
        raise HTTPException(409, "Já existe uma conta com este email")
    return user
