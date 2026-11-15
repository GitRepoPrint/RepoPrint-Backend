import hashlib
import time
import uuid
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import Text, delete, func, literal, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User, UserOAuth
from app.services import audit

PROVIDER= "github"
SCOPE= "repo"
STATE_SECONDS= 600
STATE_PURPOSE= "github-connect"


class GitHubOAuthError(Exception):
    def __init__(self, status: int, message: str):
        self.status= status
        self.message= message


def ensure_configured() -> None:
    if not (settings.github_client_id and settings.github_client_secret and settings.token_encryption_key):
        raise GitHubOAuthError(503, "A ligação ao GitHub não está configurada")


def encryption_key() -> str:
    return settings.token_encryption_key.get_secret_value()


def state_key() -> bytes:
    return hashlib.sha256(b"github-oauth-state:" + encryption_key().encode()).digest()


def authorize_url(sub: str) -> str:
    ensure_configured()
    state= jwt.encode(
        {"sub": sub, "purpose": STATE_PURPOSE, "exp": int(time.time()) + STATE_SECONDS},
        state_key(),
        algorithm="HS256",
    )
    query= urlencode({
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": SCOPE,
        "state": state,
        "allow_signup": "false",
    })
    return f"{settings.github_oauth_url}/login/oauth/authorize?{query}"


def check_state(state: str, sub: str) -> None:
    try:
        claims= jwt.decode(state, state_key(), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.InvalidTokenError:
        raise GitHubOAuthError(400, "O pedido de ligação ao GitHub é inválido ou expirou")
    if claims.get("purpose") != STATE_PURPOSE or claims["sub"] != sub:
        raise GitHubOAuthError(400, "O pedido de ligação ao GitHub é inválido ou expirou")


async def exchange_code(code: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response= await client.post(
                f"{settings.github_oauth_url}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_client_id,
                    "client_secret": settings.github_client_secret.get_secret_value(),
                    "code": code,
                    "redirect_uri": settings.github_redirect_uri,
                },
            )
    except httpx.HTTPError:
        raise GitHubOAuthError(502, "Falha ao contactar o GitHub")
    token= response.json().get("access_token") if response.status_code == 200 else None
    if not token:
        raise GitHubOAuthError(400, "O GitHub recusou o código de autorização")
    return token


async def github_account(token: str) -> tuple[str, str]:
    try:
        async with httpx.AsyncClient(base_url=settings.github_api_url, timeout=10) as client:
            response= await client.get("/user", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    except httpx.HTTPError:
        raise GitHubOAuthError(502, "Falha ao contactar o GitHub")
    if response.status_code != 200:
        raise GitHubOAuthError(502, f"GitHub respondeu {response.status_code}")
    data= response.json()
    return str(data["id"]), data["login"]


def encrypt(value: str):
    return func.pgp_sym_encrypt(literal(value, Text), literal(encryption_key(), Text))


def decrypt(column):
    return func.pgp_sym_decrypt(column, literal(encryption_key(), Text))


async def connection(session: AsyncSession, user_id: uuid.UUID) -> UserOAuth | None:
    return await session.scalar(select(UserOAuth).where(UserOAuth.user_id == user_id, UserOAuth.provider == PROVIDER))


async def connect(session: AsyncSession, user: User, code: str, state: str) -> str:
    ensure_configured()
    check_state(state, user.keycloak_sub)
    token= await exchange_code(code)
    account_id, login= await github_account(token)
    existing= await connection(session, user.id)
    try:
        if existing:
            existing.provider_user_id= account_id
            existing.provider_username= login
            existing.access_token= encrypt(token)
        else:
            session.add(UserOAuth(
                user_id=user.id,
                provider=PROVIDER,
                provider_user_id=account_id,
                provider_username=login,
                access_token=encrypt(token),
            ))
        audit.record(session, "github.connected", user_id=user.id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise GitHubOAuthError(409, "Esta conta do GitHub já está ligada a outro utilizador")
    return login


async def user_token(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    if settings.token_encryption_key is None:
        return None
    try:
        return await session.scalar(
            select(decrypt(UserOAuth.access_token)).where(UserOAuth.user_id == user_id, UserOAuth.provider == PROVIDER)
        )
    except DBAPIError:
        await session.rollback()
        raise GitHubOAuthError(409, "A ligação ao GitHub não pôde ser lida. Liga a conta outra vez.")


async def revoke(token: str) -> bool:
    try:
        async with httpx.AsyncClient(base_url=settings.github_api_url, timeout=10) as client:
            response= await client.request(
                "DELETE",
                f"/applications/{settings.github_client_id}/grant",
                auth=(settings.github_client_id, settings.github_client_secret.get_secret_value()),
                headers={"Accept": "application/vnd.github+json"},
                json={"access_token": token},
            )
    except httpx.HTTPError:
        return False
    return response.status_code in (204, 404)


async def revoke_stored(session: AsyncSession, user_id: uuid.UUID) -> bool:
    if not (settings.github_client_id and settings.github_client_secret and settings.token_encryption_key):
        return False
    try:
        token= await user_token(session, user_id)
    except GitHubOAuthError:
        return False
    return await revoke(token) if token else False


async def disconnect(session: AsyncSession, user: User) -> bool:
    ensure_configured()
    revoked= await revoke_stored(session, user.id)
    await session.execute(delete(UserOAuth).where(UserOAuth.user_id == user.id, UserOAuth.provider == PROVIDER))
    audit.record(session, "github.disconnected", user_id=user.id, details={"revoked": revoked})
    await session.commit()
    return revoked


async def drop_connection(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(delete(UserOAuth).where(UserOAuth.user_id == user_id, UserOAuth.provider == PROVIDER))
    audit.record(session, "github.connection_lost", user_id=user_id)
    await session.commit()
