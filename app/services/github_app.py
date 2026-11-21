import hashlib
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import GitHubInstallation, User
from app.services import audit, github_oauth
from app.services.github import GitHubError, fetch_repository
from app.services.github_oauth import GitHubOAuthError

STATE_SECONDS= 600
STATE_PURPOSE= "github-app-install"
JWT_SECONDS= 540
GRACE_DAYS= 7
MAX_INSTALLATION_PAGES= 5
NOT_INSTALLED= "app_not_installed"
RECONNECT= "A ligação ao GitHub deixou de ser válida. Liga a conta outra vez."


class GitHubAppError(Exception):
    def __init__(self, status: int, message: str, code: str | None= None):
        self.status= status
        self.message= message
        self.code= code


def configured() -> bool:
    return bool(
        settings.github_app_id
        and settings.github_app_slug
        and settings.github_app_client_id
        and settings.github_app_client_secret
        and settings.github_app_private_key
    )


def ensure_configured() -> None:
    if not configured():
        raise GitHubAppError(503, "A app do GitHub não está configurada")


def private_key() -> str:
    return settings.github_app_private_key.get_secret_value().replace("\\n", "\n")


def app_jwt() -> str:
    now= int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + JWT_SECONDS, "iss": str(settings.github_app_id)}, private_key(), algorithm="RS256")


def state_key() -> bytes:
    return hashlib.sha256(b"github-app-state:" + settings.github_app_client_secret.get_secret_value().encode()).digest()


def install_url(sub: str) -> str:
    ensure_configured()
    state= jwt.encode(
        {"sub": sub, "purpose": STATE_PURPOSE, "exp": int(time.time()) + STATE_SECONDS},
        state_key(),
        algorithm="HS256",
    )
    return f"{settings.github_oauth_url}/apps/{settings.github_app_slug}/installations/new?{urlencode({'state': state})}"


def check_state(state: str, sub: str) -> None:
    invalid= GitHubAppError(400, "O pedido de instalação da app é inválido ou expirou")
    try:
        claims= jwt.decode(state, state_key(), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.InvalidTokenError:
        raise invalid
    if claims.get("purpose") != STATE_PURPOSE or claims["sub"] != sub:
        raise invalid


async def api(method: str, path: str, *, token: str, json: dict | None= None, params: dict | None= None) -> httpx.Response:
    try:
        async with httpx.AsyncClient(base_url=settings.github_api_url, timeout=10) as client:
            return await client.request(
                method,
                path,
                json=json,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError:
        raise GitHubAppError(502, "Falha ao contactar o GitHub")


async def exchange_code(code: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response= await client.post(
                f"{settings.github_oauth_url}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_app_client_id,
                    "client_secret": settings.github_app_client_secret.get_secret_value(),
                    "code": code,
                    "redirect_uri": settings.github_app_redirect_uri,
                },
            )
    except httpx.HTTPError:
        raise GitHubAppError(502, "Falha ao contactar o GitHub")
    token= response.json().get("access_token") if response.status_code == 200 else None
    if not token:
        raise GitHubAppError(400, "O GitHub recusou o código de autorização")
    return token


async def user_installations(token: str) -> list[dict]:
    found: list[dict]= []
    for page in range(1, MAX_INSTALLATION_PAGES + 1):
        response= await api("GET", "/user/installations", token=token, params={"per_page": 100, "page": page})
        if response.status_code != 200:
            raise GitHubAppError(502, f"GitHub respondeu {response.status_code}")
        batch= response.json().get("installations", [])
        found.extend(batch)
        if len(batch) < 100:
            break
    return found


async def complete_install(session: AsyncSession, user: User, code: str, installation_id: int, state: str) -> str:
    ensure_configured()
    check_state(state, user.keycloak_sub)
    token= await exchange_code(code)
    found= next((item for item in await user_installations(token) if item["id"] == installation_id), None)
    if found is None:
        raise GitHubAppError(403, "Esta instalação não pertence à tua conta do GitHub")
    login= found["account"]["login"]
    await session.execute(delete(GitHubInstallation).where(
        GitHubInstallation.user_id == user.id,
        GitHubInstallation.account_login == login,
        GitHubInstallation.installation_id != installation_id,
    ))
    row= await session.scalar(select(GitHubInstallation).where(
        GitHubInstallation.user_id == user.id, GitHubInstallation.installation_id == installation_id
    ))
    if row is None:
        row= GitHubInstallation(user_id=user.id, installation_id=installation_id)
        session.add(row)
    row.account_login= login
    row.account_type= found["account"]["type"]
    row.repository_selection= found.get("repository_selection", "selected")
    row.failed_since= None
    audit.record(session, "github_app.installed", user_id=user.id, details={"account": login})
    await session.commit()
    return login


async def installation_for_repo(owner: str, repo: str) -> int | None:
    response= await api("GET", f"/repos/{owner}/{repo}/installation", token=app_jwt())
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise GitHubAppError(502, f"GitHub respondeu {response.status_code}")
    return response.json()["id"]


async def installation_token(installation_id: int, repo: str) -> str | None:
    response= await api(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        token=app_jwt(),
        json={"repositories": [repo], "permissions": {"contents": "read"}},
    )
    if response.status_code == 404:
        return None
    if response.status_code != 201:
        raise GitHubAppError(502, f"GitHub respondeu {response.status_code}")
    return response.json()["token"]


async def private_token(session: AsyncSession, user: User, owner: str, repo: str) -> str | None:
    if not configured():
        return None
    oauth= await github_oauth.user_token(session, user.id)
    if not oauth:
        return None
    try:
        info= await fetch_repository(owner, repo, token=oauth)
    except GitHubError as error:
        if error.status == 404:
            return None
        if error.status == 401:
            await github_oauth.drop_connection(session, user.id)
            raise GitHubOAuthError(409, RECONNECT)
        raise
    if not info["private"]:
        return None
    full_owner, full_repo= info["full_name"].split("/", 1)
    installation_id= await installation_for_repo(full_owner, full_repo)
    token= await installation_token(installation_id, full_repo) if installation_id else None
    if token is None:
        raise GitHubAppError(404, "Instala a app do RepoPrint para ler este repositório privado", NOT_INSTALLED)
    return token


async def still_installed(installation_id: int) -> bool | None:
    try:
        response= await api("GET", f"/app/installations/{installation_id}", token=app_jwt())
    except GitHubAppError:
        return None
    if response.status_code == 200:
        return True
    return False if response.status_code == 404 else None


async def installations(session: AsyncSession, user: User) -> list[GitHubInstallation]:
    rows= list(await session.scalars(
        select(GitHubInstallation).where(GitHubInstallation.user_id == user.id).order_by(GitHubInstallation.created_at)
    ))
    if not rows or not configured():
        return rows
    now= datetime.now(timezone.utc)
    kept= []
    for row in rows:
        alive= await still_installed(row.installation_id)
        if alive:
            row.failed_since= None
        elif alive is False and row.failed_since is None:
            row.failed_since= now
        elif alive is False and now - row.failed_since > timedelta(days=GRACE_DAYS):
            await session.delete(row)
            continue
        kept.append(row)
    await session.commit()
    return kept


async def remove(session: AsyncSession, user: User, installation_id: int) -> bool:
    result= await session.execute(delete(GitHubInstallation).where(
        GitHubInstallation.user_id == user.id, GitHubInstallation.installation_id == installation_id
    ).returning(GitHubInstallation.id))
    removed= result.first() is not None
    if removed:
        audit.record(session, "github_app.removed", user_id=user.id)
    await session.commit()
    return removed
