import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import settings
from app.models import AuditLog, GitHubInstallation, User, UserOAuth
from app.routers import dfd as dfd_router
from app.services import github_app, github_oauth
from app.services.github import GitHubError
from app.services.github_app import GitHubAppError

from test_auth import bearer, client, keycloak, make_token
from test_dfd_schema import FILES, archive_of

pytestmark= pytest.mark.anyio

APP_KEY= rsa.generate_private_key(public_exponent=65537, key_size=2048)
APP_PEM= APP_KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
INSTALL_TOKEN= "ghs_install"
USER_OAUTH= "gho_ana"
URL= "https://github.com/org/priv"


class FakeGitHubApp:
    def __init__(self):
        self.visible= {777: {"id": 777, "account": {"login": "org", "type": "Organization"}, "repository_selection": "selected"}}
        self.on_repo= {"org/Priv": 777}
        self.gone= set()
        self.private= {"org/Priv"}
        self.oauth_sees= {"org/Priv", "pub/Lic"}
        self.token_requests= []
        self.token_bearers= []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path= request.url.path
        auth= request.headers.get("authorization", "").removeprefix("Bearer ")
        if path== "/login/oauth/access_token":
            form= {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form["code"]== "code-ok" and form["client_secret"]== "app-secret" and form["client_id"]== "app-cid":
                return httpx.Response(200, json={"access_token": "ghu_user"})
            return httpx.Response(200, json={"error": "bad_verification_code"})
        if path== "/user/installations":
            visible= list(self.visible.values()) if auth== "ghu_user" else []
            return httpx.Response(200, json={"installations": visible})
        if path.endswith("/installation"):
            claims= jwt.decode(auth, APP_KEY.public_key(), algorithms=["RS256"])
            assert claims["iss"]== "123"
            name= "/".join(path.split("/")[2:4])
            found= self.on_repo.get(name)
            return httpx.Response(200, json={"id": found}) if found else httpx.Response(404)
        if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
            installation= int(path.split("/")[3])
            self.token_requests.append(json.loads(request.content))
            self.token_bearers.append(auth)
            return httpx.Response(404) if installation in self.gone else httpx.Response(201, json={"token": INSTALL_TOKEN})
        if path.startswith("/app/installations/"):
            installation= int(path.split("/")[3])
            return httpx.Response(404) if installation in self.gone else httpx.Response(200, json={"id": installation})
        if path.startswith("/repos/") and path.count("/")== 3:
            name= "/".join(path.split("/")[2:4])
            known= {n.lower(): n for n in self.oauth_sees}
            if auth== USER_OAUTH and name.lower() in known:
                full= known[name.lower()]
                return httpx.Response(200, json={"full_name": full, "private": full in self.private, "default_branch": "main"})
            return httpx.Response(404)
        return httpx.Response(500)


@pytest.fixture
def app_env(monkeypatch):
    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_app_slug", "repoprint-test")
    monkeypatch.setattr(settings, "github_app_client_id", "app-cid")
    monkeypatch.setattr(settings, "github_app_client_secret", SecretStr("app-secret"))
    monkeypatch.setattr(settings, "github_app_private_key", SecretStr(APP_PEM.replace("\n", "\\n")))
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr("k" * 32))


@pytest.fixture
def fake(monkeypatch, app_env):
    hub= FakeGitHubApp()
    real= httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(hub.handler), **kwargs))
    return hub


async def make_user(session, sub="sub-1", email="ana@example.com", *, oauth=True):
    user= User(keycloak_sub=sub, email=email)
    session.add(user)
    await session.flush()
    if oauth:
        session.add(UserOAuth(
            user_id=user.id,
            provider="github",
            provider_user_id=f"gh-{sub}",
            provider_username=f"gh-{sub}",
            access_token=github_oauth.encrypt(USER_OAUTH),
        ))
        await session.flush()
    return user


def headers(**token_args):
    return bearer(make_token(**token_args))


async def install_state(client, **token_args):
    url= (await client.get("/github/app/install", headers=headers(**token_args))).json()["url"]
    return parse_qs(urlparse(url).query)["state"][0]


async def finish(client, state, *, code="code-ok", installation_id=777, **token_args):
    body= {"code": code, "installation_id": installation_id, "state": state}
    return await client.post("/github/app/callback", json=body, headers=headers(**token_args))


async def rows(session):
    return list(await session.scalars(select(GitHubInstallation)))


async def test_install_is_unavailable_without_configuration(client):
    response= await client.get("/github/app/install", headers=headers())

    assert response.status_code== 503


async def test_install_url_points_to_the_app_with_a_signed_state(client, fake):
    response= await client.get("/github/app/install", headers=headers())

    url= urlparse(response.json()["url"])
    assert response.status_code== 200
    assert (url.netloc, url.path)== ("github.com", "/apps/repoprint-test/installations/new")
    claims= jwt.decode(parse_qs(url.query)["state"][0], github_app.state_key(), algorithms=["HS256"])
    assert claims["sub"]== "sub-1" and claims["purpose"]== "github-app-install"


async def test_callback_stores_the_installation_without_any_token(client, session, fake):
    state= await install_state(client)

    response= await finish(client, state)

    assert response.status_code== 200 and response.json()== {"login": "org"}
    stored= await rows(session)
    assert len(stored)== 1
    assert (stored[0].installation_id, stored[0].account_login, stored[0].account_type, stored[0].repository_selection)== (777, "org", "Organization", "selected")
    assert stored[0].failed_since is None
    assert "github_app.installed" in list(await session.scalars(select(AuditLog.action)))


async def test_callback_refuses_an_installation_the_user_cannot_see(client, session, fake):
    state= await install_state(client)

    response= await finish(client, state, installation_id=999)

    assert response.status_code== 403
    assert await rows(session)== []


async def test_callback_refuses_a_code_github_rejects(client, session, fake):
    state= await install_state(client)

    response= await finish(client, state, code="wrong")

    assert response.status_code== 400
    assert await rows(session)== []


async def test_callback_refuses_a_forged_or_foreign_state(client, session, fake):
    foreign= await install_state(client, sub="sub-2", email="bia@example.com")

    assert (await finish(client, "not-a-state")).status_code== 400
    assert (await finish(client, foreign)).status_code== 400
    assert await rows(session)== []


async def test_reinstalling_replaces_the_old_row_of_the_same_account(client, session, fake):
    await finish(client, await install_state(client))
    fake.visible= {888: {"id": 888, "account": {"login": "org", "type": "Organization"}, "repository_selection": "all"}}

    await finish(client, await install_state(client), installation_id=888)

    stored= await rows(session)
    assert [(r.installation_id, r.repository_selection) for r in stored]== [(888, "all")]


async def test_status_lists_the_installations_and_hides_nothing_secret(client, session, fake):
    await finish(client, await install_state(client))

    response= await client.get("/github/app/status", headers=headers())

    assert response.json()== {
        "configured": True,
        "installations": [
            {"installation_id": 777, "account_login": "org", "account_type": "Organization", "repository_selection": "selected", "failing": False}
        ],
    }


async def test_status_without_configuration_is_empty_and_not_an_error(client):
    response= await client.get("/github/app/status", headers=headers())

    assert response.status_code== 200 and response.json()== {"configured": False, "installations": []}


async def test_a_vanished_installation_is_flagged_then_removed_after_seven_days(client, session, fake):
    await finish(client, await install_state(client))
    fake.gone.add(777)

    first= await client.get("/github/app/status", headers=headers())
    assert first.json()["installations"][0]["failing"] is True

    row= (await rows(session))[0]
    row.failed_since= datetime.now(timezone.utc) - timedelta(days=github_app.GRACE_DAYS - 1)
    await session.flush()
    assert len((await client.get("/github/app/status", headers=headers())).json()["installations"])== 1

    row.failed_since= datetime.now(timezone.utc) - timedelta(days=github_app.GRACE_DAYS + 1)
    await session.flush()
    assert (await client.get("/github/app/status", headers=headers())).json()["installations"]== []
    assert await rows(session)== []


async def test_an_installation_that_comes_back_clears_the_flag(client, session, fake):
    await finish(client, await install_state(client))
    fake.gone.add(777)
    await client.get("/github/app/status", headers=headers())
    fake.gone.clear()

    response= await client.get("/github/app/status", headers=headers())

    assert response.json()["installations"][0]["failing"] is False


async def test_removing_deletes_only_the_callers_row(client, session, fake):
    await finish(client, await install_state(client))

    other= await client.delete("/github/app/installations/777", headers=headers(sub="sub-2", email="bia@example.com"))
    own= await client.delete("/github/app/installations/777", headers=headers())

    assert other.status_code== 404 and own.status_code== 204
    assert await rows(session)== []


async def test_every_app_endpoint_needs_a_token(client):
    assert (await client.get("/github/app/install")).status_code== 401
    assert (await client.get("/github/app/status")).status_code== 401
    assert (await client.delete("/github/app/installations/1")).status_code== 401
    assert (await client.post("/github/app/callback", json={"code": "c", "installation_id": 1, "state": "s"})).status_code== 401


async def test_the_deleted_account_takes_its_installations_with_it(client, session, fake):
    await finish(client, await install_state(client))
    user= await session.scalar(select(User))

    await session.delete(user)
    await session.flush()

    assert await session.scalar(select(func.count()).select_from(GitHubInstallation))== 0


async def test_a_private_repository_gets_a_one_repo_read_only_token(session, fake):
    user= await make_user(session)

    token= await github_app.private_token(session, user, "org", "priv")

    assert token== INSTALL_TOKEN
    assert fake.token_requests== [{"repositories": ["Priv"], "permissions": {"contents": "read"}}]
    claims= jwt.decode(fake.token_bearers[0], APP_KEY.public_key(), algorithms=["RS256"])
    assert claims["exp"] - claims["iat"] <= 600


async def test_the_installation_token_needs_the_user_to_prove_access_with_oauth(session, fake):
    no_oauth= await make_user(session, oauth=False)
    fake.oauth_sees= set()
    blind= await make_user(session, sub="sub-2", email="bia@example.com")

    assert await github_app.private_token(session, no_oauth, "org", "priv") is None
    assert await github_app.private_token(session, blind, "org", "priv") is None
    assert fake.token_requests== []


async def test_a_public_repository_never_gets_an_installation_token(session, fake):
    user= await make_user(session)
    fake.on_repo["pub/Lic"]= 777

    assert await github_app.private_token(session, user, "pub", "lic") is None
    assert fake.token_requests== []


async def test_a_private_repository_without_the_app_says_so(session, fake):
    user= await make_user(session)
    fake.on_repo.clear()

    with pytest.raises(GitHubAppError) as error:
        await github_app.private_token(session, user, "org", "priv")

    assert (error.value.status, error.value.code)== (404, "app_not_installed")


async def test_without_configuration_nothing_private_is_read(session, app_env, monkeypatch):
    user= await make_user(session)
    monkeypatch.setattr(settings, "github_app_id", None)

    assert await github_app.private_token(session, user, "org", "priv") is None


@pytest.fixture
def downloads(monkeypatch):
    calls= []

    async def fake_download(owner, repo, token=None):
        calls.append((owner, repo, token))
        if (owner, repo)== ("org", "priv") and token != INSTALL_TOKEN:
            raise GitHubError(404, "Repositório não encontrado ou privado")
        return archive_of(FILES)

    monkeypatch.setattr(dfd_router, "download_archive", fake_download)
    return calls


async def test_dfd_reads_a_private_repository_with_the_installation_token(client, session, fake, downloads):
    await make_user(session)

    response= await client.get("/dfd", params={"url": URL}, headers=headers())

    assert response.status_code== 200
    assert downloads== [("org", "priv", INSTALL_TOKEN)]


async def test_dfd_without_a_session_stays_public_and_never_uses_the_app(client, fake, downloads):
    response= await client.get("/dfd", params={"url": "https://github.com/pub/lic"})

    assert response.status_code== 200
    assert downloads== [("pub", "lic", None)]
    assert fake.token_requests== []


async def test_dfd_asks_to_install_the_app_when_the_user_proved_access(client, session, fake, downloads):
    await make_user(session)
    fake.on_repo.clear()

    response= await client.get("/dfd", params={"url": URL}, headers=headers())

    assert response.status_code== 404
    assert response.json()["detail"]["code"]== "app_not_installed"
    assert downloads== []


async def test_dfd_hides_the_private_repository_from_someone_without_access(client, session, fake, downloads):
    await make_user(session, sub="sub-2", email="bia@example.com", oauth=False)

    response= await client.get("/dfd", params={"url": URL}, headers=headers(sub="sub-2", email="bia@example.com"))

    assert response.status_code== 404
    assert isinstance(response.json()["detail"], str)
    assert downloads== [("org", "priv", None)]


async def test_a_private_graph_is_not_served_from_the_cache_to_another_user(client, session, fake, downloads):
    await make_user(session)
    await make_user(session, sub="sub-2", email="bia@example.com", oauth=False)
    first= await client.get("/dfd", params={"url": URL}, headers=headers())
    repeat= await client.get("/dfd", params={"url": URL}, headers=headers())
    other= await client.get("/dfd", params={"url": URL}, headers=headers(sub="sub-2", email="bia@example.com"))
    anonymous= await client.get("/dfd", params={"url": URL})

    assert (first.status_code, repeat.status_code)== (200, 200)
    assert (other.status_code, anonymous.status_code)== (404, 404)
    assert [call[2] for call in downloads]== [INSTALL_TOKEN, None, None]


async def test_the_stream_also_uses_the_installation_token(client, session, fake, downloads):
    await make_user(session)

    async with client.stream("GET", "/dfd/stream", params={"url": URL}, headers=headers()) as response:
        events= [json.loads(line) async for line in response.aiter_lines() if line.strip()]

    assert response.status_code== 200 and events[-1]["type"]== "result"
    assert downloads== [("org", "priv", INSTALL_TOKEN)]


async def test_the_per_request_token_reaches_the_archive_and_the_file_listing(monkeypatch):
    from app.services import code_fetcher

    seen= []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(404)

    real= httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(settings, "github_token", "server-token")
    monkeypatch.setattr(settings, "dfd_fetch_method", "auto")

    with pytest.raises(GitHubError):
        await code_fetcher.download_archive("o", "r", "per-request")
    with pytest.raises(GitHubError):
        await code_fetcher.fetch_repository_files("o", "r", token="per-request")
    with pytest.raises(GitHubError):
        await code_fetcher.download_archive("o", "r")

    assert seen== ["Bearer per-request", "Bearer per-request", "Bearer server-token"]
