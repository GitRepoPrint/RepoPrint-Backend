import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import settings
from app.models import AnalysisRun, AuditLog, Repository, User
from app.services import account, audit, keycloak_admin
from app.services.keycloak_admin import KeycloakUnavailable

from test_auth import bearer, client, keycloak, make_token
from test_database_models import build_account

pytestmark= pytest.mark.anyio


async def actions(session):
    return list(await session.scalars(select(AuditLog.action).order_by(AuditLog.timestamp, AuditLog.action)))


async def signed_up_account(session, sub="sub-1", email="ana@example.com"):
    user= await build_account(session, email)
    user.keycloak_sub= sub
    await session.flush()
    return user


async def test_export_has_the_users_data_and_no_tokens(client, session):
    await signed_up_account(session)

    response= await client.get("/me/export", headers=bearer(make_token()))

    body= response.json()
    assert response.status_code== 200
    assert response.headers["content-disposition"]== 'attachment; filename="repoprint-data.json"'
    assert body["account"]["email"]== "ana@example.com"
    assert [item["provider"] for item in body["linked_accounts"]]== ["github"]
    assert len(body["repositories"])== len(body["analysis_runs"])== len(body["threat_findings"])== 1
    assert [message["message"] for message in body["chat_messages"]]== ["olá"]
    assert [log["action"] for log in body["audit_logs"]]== ["analysis.run"]
    assert "access_token" not in response.text and "refresh_token" not in response.text


async def test_export_leaves_out_other_peoples_data(client, session):
    await signed_up_account(session)
    other= await build_account(session, "outra@example.com")
    other.keycloak_sub= "sub-2"
    await session.flush()

    body= (await client.get("/me/export", headers=bearer(make_token()))).json()

    assert body["account"]["email"]== "ana@example.com"
    assert len(body["repositories"])== len(body["audit_logs"])== 1
    assert "outra@example.com" not in json.dumps(body)


async def test_export_is_recorded_in_the_audit_log(client, session):
    await client.get("/me/export", headers=bearer(make_token()))

    assert "account.exported" in await actions(session)


async def test_export_requires_a_token(client):
    assert (await client.get("/me/export")).status_code== 401


@pytest.fixture
def deleted_in_keycloak(monkeypatch):
    calls= []

    async def delete_user(sub):
        calls.append(sub)

    monkeypatch.setattr(account.keycloak_admin, "delete_user", delete_user)
    return calls


async def test_deleting_the_account_removes_data_and_the_keycloak_user(client, session, deleted_in_keycloak):
    await signed_up_account(session)

    response= await client.delete("/me", headers=bearer(make_token()))

    assert response.status_code== 204
    assert deleted_in_keycloak== ["sub-1"]
    assert await session.scalar(select(func.count()).select_from(User))== 0
    assert await session.scalar(select(func.count()).select_from(Repository))== 0
    assert await session.scalar(select(func.count()).select_from(AnalysisRun))== 0


async def test_audit_logs_outlive_the_account_without_the_user(client, session, deleted_in_keycloak):
    await signed_up_account(session)

    await client.delete("/me", headers=bearer(make_token()))

    logs= list(await session.scalars(select(AuditLog)))
    assert sorted(log.action for log in logs)== ["account.deleted", "analysis.run"]
    assert all(log.user_id is None for log in logs)


async def test_deleting_an_account_that_has_no_row_still_cleans_keycloak(client, session, deleted_in_keycloak):
    response= await client.delete("/me", headers=bearer(make_token(sub="sub-9")))

    assert response.status_code== 204
    assert deleted_in_keycloak== ["sub-9"]
    log= await session.scalar(select(AuditLog).where(AuditLog.action== "account.deleted"))
    assert log.details== {"had_account": False}


async def test_a_keycloak_failure_gives_503_and_a_retry_finishes_the_job(client, session, monkeypatch):
    await signed_up_account(session)
    outcomes= [KeycloakUnavailable("em baixo"), None]
    calls= []

    async def flaky(sub):
        calls.append(sub)
        outcome= outcomes.pop(0)
        if outcome:
            raise outcome

    monkeypatch.setattr(account.keycloak_admin, "delete_user", flaky)

    first= await client.delete("/me", headers=bearer(make_token()))
    assert first.status_code== 503
    assert await session.scalar(select(func.count()).select_from(User))== 0

    second= await client.delete("/me", headers=bearer(make_token()))
    assert second.status_code== 204
    assert calls== ["sub-1", "sub-1"]


async def test_deleting_requires_a_token(client):
    assert (await client.delete("/me")).status_code== 401


async def test_the_first_login_is_recorded_once(client, session):
    await client.get("/me", headers=bearer(make_token()))
    await client.get("/me", headers=bearer(make_token()))

    assert (await actions(session)).count("account.created")== 1
    log= await session.scalar(select(AuditLog))
    assert log.user_id is not None and log.details is None


async def test_a_changed_email_and_name_follow_keycloak(client, session):
    first= (await client.get("/me", headers=bearer(make_token()))).json()

    second= (await client.get("/me", headers=bearer(make_token(email="nova@example.com", name="Ana Silva")))).json()

    assert second["id"]== first["id"]
    assert (second["email"], second["name"])== ("nova@example.com", "Ana Silva")


async def test_a_changed_email_that_belongs_to_someone_else_is_refused(client, session):
    await client.get("/me", headers=bearer(make_token(sub="sub-1", email="ana@example.com")))
    await client.get("/me", headers=bearer(make_token(sub="sub-2", email="bia@example.com")))

    response= await client.get("/me", headers=bearer(make_token(sub="sub-2", email="ana@example.com")))

    assert response.status_code== 409


async def test_purge_removes_only_logs_older_than_the_retention(session):
    now= datetime.now(timezone.utc)
    session.add_all([
        AuditLog(action="velho", timestamp=now - timedelta(days=91)),
        AuditLog(action="recente", timestamp=now - timedelta(days=89)),
    ])
    await session.flush()

    removed= await audit.purge_older_than(session)

    assert removed== 1
    assert await actions(session)== ["recente"]


def fake_keycloak(monkeypatch, *, delete_status=204, token_status=200, token_expires=300):
    seen= {"tokens": 0, "deletes": [], "auth": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            seen["tokens"]+= 1
            if token_status != 200:
                return httpx.Response(token_status)
            return httpx.Response(200, json={"access_token": f"admin-{seen['tokens']}", "expires_in": token_expires})
        seen["deletes"].append(str(request.url))
        seen["auth"].append(request.headers["authorization"])
        status= delete_status.pop(0) if isinstance(delete_status, list) else delete_status
        return httpx.Response(status)

    real= httpx.AsyncClient
    monkeypatch.setattr(keycloak_admin.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", SecretStr("segredo"))
    keycloak_admin.reset_token()
    return seen


async def test_keycloak_delete_hits_the_admin_api_with_a_service_account_token(monkeypatch):
    seen= fake_keycloak(monkeypatch)

    await keycloak_admin.delete_user("sub-1")

    assert seen["deletes"]== [f"{settings.admin_url}/users/sub-1"]
    assert seen["auth"]== ["Bearer admin-1"]


async def test_keycloak_treats_a_missing_user_as_deleted(monkeypatch):
    fake_keycloak(monkeypatch, delete_status=404)

    await keycloak_admin.delete_user("sub-1")


async def test_keycloak_token_is_reused(monkeypatch):
    seen= fake_keycloak(monkeypatch)

    await keycloak_admin.delete_user("sub-1")
    await keycloak_admin.delete_user("sub-2")

    assert seen["tokens"]== 1


async def test_keycloak_retries_once_with_a_new_token_after_401(monkeypatch):
    seen= fake_keycloak(monkeypatch, delete_status=[401, 204])

    await keycloak_admin.delete_user("sub-1")

    assert seen["auth"]== ["Bearer admin-1", "Bearer admin-2"]


@pytest.mark.parametrize("failure", [{"delete_status": 500}, {"token_status": 401}])
async def test_keycloak_errors_are_reported_as_unavailable(monkeypatch, failure):
    fake_keycloak(monkeypatch, **failure)

    with pytest.raises(KeycloakUnavailable):
        await keycloak_admin.delete_user("sub-1")


async def test_keycloak_without_the_admin_secret_is_unavailable(monkeypatch):
    fake_keycloak(monkeypatch)
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", None)

    with pytest.raises(KeycloakUnavailable):
        await keycloak_admin.delete_user("sub-1")


async def test_keycloak_network_failure_is_unavailable(monkeypatch):
    def down(request):
        raise httpx.ConnectError("sem ligação")

    real= httpx.AsyncClient
    monkeypatch.setattr(keycloak_admin.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(down), **kwargs))
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", SecretStr("segredo"))
    keycloak_admin.reset_token()

    with pytest.raises(KeycloakUnavailable):
        await keycloak_admin.delete_user("sub-1")
