import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import settings
from app.models import AuditLog, User, UserOAuth
from app.services import account, github, github_oauth, keycloak_admin
from app.services.github_oauth import GitHubOAuthError

from conftest import FakeGitHub
from test_auth import bearer, client, keycloak, make_token

pytestmark= pytest.mark.anyio

KEY= "k" * 32


class FakeHub:
    def __init__(self, repos: FakeGitHub):
        self.repos= repos
        self.codes= {"code-ana": ("gho_ana", "101", "ana-gh"), "code-bia": ("gho_bia", "202", "bia-gh"), "code-ana2": ("gho_ana2", "101", "ana-gh")}
        self.revoked= []
        self.revoke_status= 204
        self.oauth_calls= []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path= request.url.path
        if path== "/login/oauth/access_token":
            form= {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.oauth_calls.append(form)
            entry= self.codes.get(form["code"])
            if not entry or form["client_secret"] != "csecret":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": entry[0], "scope": "repo", "token_type": "bearer"})
        if path== "/user":
            token= request.headers["authorization"].removeprefix("Bearer ")
            for access, account_id, login in self.codes.values():
                if access== token:
                    return httpx.Response(200, json={"id": int(account_id), "login": login})
            return httpx.Response(401)
        if path.startswith("/applications/") and path.endswith("/grant"):
            self.revoked.append({"path": path, "auth": request.headers["authorization"], "body": json.loads(request.content)})
            return httpx.Response(self.revoke_status)
        return self.repos.handler(request)


@pytest.fixture
def github_app(monkeypatch):
    monkeypatch.setattr(settings, "github_client_id", "cid")
    monkeypatch.setattr(settings, "github_client_secret", SecretStr("csecret"))
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(KEY))


@pytest.fixture
def hub(monkeypatch, github_app):
    fake= FakeHub(FakeGitHub(sample=[], closed=[]))
    real= httpx.AsyncClient
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(fake.handler), **kwargs))
    return fake


async def connect_as(client, hub, code, **token_args):
    url= (await client.get("/github/connect", headers=bearer(make_token(**token_args)))).json()["url"]
    state= parse_qs(urlparse(url).query)["state"][0]
    return await client.post("/github/callback", json={"code": code, "state": state}, headers=bearer(make_token(**token_args)))


async def stored(session):
    return list(await session.scalars(select(UserOAuth)))


async def audit_actions(session):
    return list(await session.scalars(select(AuditLog.action).order_by(AuditLog.timestamp, AuditLog.action)))


async def test_connect_url_asks_for_the_repo_scope_with_a_signed_state(client, hub):
    response= await client.get("/github/connect", headers=bearer(make_token()))

    query= parse_qs(urlparse(response.json()["url"]).query)
    assert response.status_code== 200
    assert response.json()["url"].startswith("https://github.com/login/oauth/authorize?")
    assert query["client_id"]== ["cid"] and query["scope"]== ["repo"] and query["allow_signup"]== ["false"]
    assert query["redirect_uri"]== [settings.github_redirect_uri]
    claims= jwt.decode(query["state"][0], github_oauth.state_key(), algorithms=["HS256"])
    assert claims["sub"]== "sub-1"


async def test_connect_is_unavailable_without_configuration(client):
    response= await client.get("/github/connect", headers=bearer(make_token()))

    assert response.status_code== 503


async def test_callback_stores_the_token_encrypted_and_reports_the_login(client, session, hub):
    response= await connect_as(client, hub, "code-ana")

    assert response.status_code== 200 and response.json()== {"login": "ana-gh"}
    row= (await stored(session))[0]
    assert row.provider_username== "ana-gh" and row.provider_user_id== "101"
    assert b"gho_ana" not in bytes(row.access_token)
    user= await session.scalar(select(User))
    assert await github_oauth.user_token(session, user.id)== "gho_ana"
    assert hub.oauth_calls[0]["redirect_uri"]== settings.github_redirect_uri


async def test_the_token_is_never_returned_by_the_api(client, session, hub):
    callback= await connect_as(client, hub, "code-ana")
    status= await client.get("/github/status", headers=bearer(make_token()))
    export= await client.get("/me/export", headers=bearer(make_token()))

    for response in (callback, status, export):
        assert "gho_ana" not in response.text and "access_token" not in response.text
    assert export.json()["linked_accounts"][0]["provider_username"]== "ana-gh"


async def test_status_before_and_after_connecting(client, hub):
    before= await client.get("/github/status", headers=bearer(make_token()))
    await connect_as(client, hub, "code-ana")
    after= await client.get("/github/status", headers=bearer(make_token()))

    assert before.json()== {"connected": False, "login": None}
    assert after.json()== {"connected": True, "login": "ana-gh"}


async def test_connecting_again_replaces_the_token(client, session, hub):
    await connect_as(client, hub, "code-ana")
    await connect_as(client, hub, "code-ana2")

    assert len(await stored(session))== 1
    user= await session.scalar(select(User))
    assert await github_oauth.user_token(session, user.id)== "gho_ana2"


@pytest.mark.parametrize("mode", ["tampered", "expired", "other_user", "wrong_purpose"])
async def test_a_bad_state_is_refused(client, session, hub, mode):
    url= (await client.get("/github/connect", headers=bearer(make_token()))).json()["url"]
    state= parse_qs(urlparse(url).query)["state"][0]
    if mode== "tampered":
        state= state[:-3] + ("aaa" if not state.endswith("aaa") else "bbb")
    if mode== "expired":
        state= jwt.encode({"sub": "sub-1", "purpose": "github-connect", "exp": int(time.time()) - 5}, github_oauth.state_key(), algorithm="HS256")
    if mode== "wrong_purpose":
        state= jwt.encode({"sub": "sub-1", "purpose": "outra-coisa", "exp": int(time.time()) + 60}, github_oauth.state_key(), algorithm="HS256")
    token_args= {"sub": "sub-2", "email": "bia@example.com"} if mode== "other_user" else {}

    response= await client.post("/github/callback", json={"code": "code-ana", "state": state}, headers=bearer(make_token(**token_args)))

    assert response.status_code== 400
    assert await stored(session)== []
    assert hub.oauth_calls== []


async def test_github_rejecting_the_code_is_a_400(client, session, hub):
    response= await connect_as(client, hub, "codigo-errado")

    assert response.status_code== 400 and await stored(session)== []


async def test_a_github_account_can_only_belong_to_one_user(client, session, hub):
    await connect_as(client, hub, "code-ana")
    hub.codes["code-bia"]= ("gho_bia", "101", "ana-gh")

    response= await connect_as(client, hub, "code-bia", sub="sub-2", email="bia@example.com")

    assert response.status_code== 409
    assert len(await stored(session))== 1


async def test_the_wrong_key_makes_the_token_unreadable(client, session, hub, monkeypatch):
    await connect_as(client, hub, "code-ana")
    user= await session.scalar(select(User))
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr("outra-chave-com-32-caracteres!!!"))

    with pytest.raises(GitHubOAuthError) as error:
        await github_oauth.user_token(session, user.id)

    assert error.value.status== 409


async def test_disconnecting_revokes_the_token_and_removes_the_link(client, session, hub):
    await connect_as(client, hub, "code-ana")

    response= await client.delete("/github/connection", headers=bearer(make_token()))

    assert response.status_code== 204
    assert await stored(session)== []
    assert hub.revoked[0]["path"]== "/applications/cid/grant"
    assert hub.revoked[0]["body"]== {"access_token": "gho_ana"}
    assert hub.revoked[0]["auth"].startswith("Basic ")
    log= await session.scalar(select(AuditLog).where(AuditLog.action== "github.disconnected"))
    assert log.details== {"revoked": True}


async def test_disconnecting_still_removes_the_link_when_the_revoke_fails(client, session, hub):
    await connect_as(client, hub, "code-ana")
    hub.revoke_status= 500

    response= await client.delete("/github/connection", headers=bearer(make_token()))

    assert response.status_code== 204 and await stored(session)== []
    log= await session.scalar(select(AuditLog).where(AuditLog.action== "github.disconnected"))
    assert log.details== {"revoked": False}


async def test_deleting_the_account_revokes_the_github_token(client, session, hub, monkeypatch):
    await connect_as(client, hub, "code-ana")

    async def delete_user(sub):
        pass

    monkeypatch.setattr(account.keycloak_admin, "delete_user", delete_user)
    response= await client.delete("/me", headers=bearer(make_token()))

    assert response.status_code== 204
    assert [item["body"] for item in hub.revoked]== [{"access_token": "gho_ana"}]
    assert await stored(session)== []


async def test_connecting_and_disconnecting_are_audited(client, session, hub):
    await connect_as(client, hub, "code-ana")
    await client.delete("/github/connection", headers=bearer(make_token()))

    actions= await audit_actions(session)
    assert "github.connected" in actions and "github.disconnected" in actions


def api_calls(hub):
    return [request for request in hub.repos.calls if request.url.path.startswith("/repos/")]


async def test_activity_uses_the_users_token(client, session, hub):
    await connect_as(client, hub, "code-ana")

    response= await client.get("/github/activity", params={"url": "https://github.com/o/r"}, headers=bearer(make_token()))

    assert response.status_code== 200
    assert {request.headers["authorization"] for request in api_calls(hub)}== {"Bearer gho_ana"}


async def test_a_private_repository_needs_the_users_token(client, session, hub):
    hub.repos.private= True

    without= await client.get("/github/activity", params={"url": "https://github.com/o/p"}, headers=bearer(make_token()))
    await connect_as(client, hub, "code-ana")
    with_link= await client.get("/github/activity", params={"url": "https://github.com/o/p"}, headers=bearer(make_token()))

    assert without.status_code== 404
    assert with_link.status_code== 200


async def test_private_results_are_not_shared_between_users(client, session, hub):
    hub.repos.private= True
    await connect_as(client, hub, "code-ana")
    await connect_as(client, hub, "code-bia", sub="sub-2", email="bia@example.com")
    url= {"url": "https://github.com/o/p"}

    first= await client.get("/github/activity", params=url, headers=bearer(make_token()))
    other= await client.get("/github/activity", params=url, headers=bearer(make_token(sub="sub-2", email="bia@example.com")))
    again= await client.get("/github/activity", params=url, headers=bearer(make_token()))

    assert (first.json()["cached"], other.json()["cached"], again.json()["cached"])== (False, False, True)
    tokens= [request.headers["authorization"] for request in api_calls(hub)]
    assert "Bearer gho_ana" in tokens and "Bearer gho_bia" in tokens


async def test_a_token_rejected_by_github_drops_the_link_and_asks_to_reconnect(client, session, hub):
    await connect_as(client, hub, "code-ana")
    hub.repos.unauthorized= True

    response= await client.get("/github/activity", params={"url": "https://github.com/o/r"}, headers=bearer(make_token()))

    assert response.status_code== 409
    assert await stored(session)== []
    assert "github.connection_lost" in await audit_actions(session)


async def test_analyses_are_audited_with_the_repository_and_the_outcome(client, session, hub):
    await client.get("/github/activity", params={"url": "https://github.com/o/r"}, headers=bearer(make_token()))
    hub.repos.missing= True
    await client.get("/github/activity", params={"url": "https://github.com/o/nada"}, headers=bearer(make_token()))

    logs= list(await session.scalars(select(AuditLog).where(AuditLog.action== "analysis.run").order_by(AuditLog.timestamp)))
    assert [log.details for log in logs]== [
        {"repository": "o/r", "outcome": "ok"},
        {"repository": "o/nada", "outcome": "not_found"},
    ]
    assert all(log.user_id is not None for log in logs)


async def test_activity_without_a_github_configuration_still_works_for_public_repositories(client, session, monkeypatch):
    fake= FakeGitHub()
    real= httpx.AsyncClient
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(fake.handler), **kwargs))

    response= await client.get("/github/activity", params={"url": "https://github.com/o/r"}, headers=bearer(make_token()))

    assert response.status_code== 200
