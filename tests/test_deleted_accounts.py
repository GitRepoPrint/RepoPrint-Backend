import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import settings
from app.models import User
from app.services import keycloak_admin
from app.services.keycloak_admin import KeycloakUnavailable

from test_account import fake_keycloak
from test_auth import bearer, client, keycloak, make_token

pytestmark= pytest.mark.anyio


@pytest.fixture
def keycloak_users(monkeypatch):
    state= {"exists": True, "error": None, "asked": []}

    async def user_exists(sub):
        state["asked"].append(sub)
        if state["error"]:
            raise state["error"]
        return state["exists"]

    monkeypatch.setattr(settings, "keycloak_admin_client_secret", SecretStr("segredo"))
    monkeypatch.setattr(keycloak_admin, "user_exists", user_exists)
    return state


async def count_users(session):
    return await session.scalar(select(func.count()).select_from(User))


async def test_a_new_account_is_only_created_while_the_keycloak_user_exists(client, session, keycloak_users):
    response= await client.get("/me", headers=bearer(make_token()))

    assert response.status_code== 200
    assert keycloak_users["asked"]== ["sub-1"]


async def test_a_token_of_a_deleted_user_does_not_recreate_the_account(client, session, keycloak_users):
    keycloak_users["exists"]= False

    response= await client.get("/me", headers=bearer(make_token()))

    assert response.status_code== 401
    assert await count_users(session)== 0


async def test_keycloak_is_not_asked_again_for_existing_accounts(client, session, keycloak_users):
    await client.get("/me", headers=bearer(make_token()))
    await client.get("/me", headers=bearer(make_token()))

    assert keycloak_users["asked"]== ["sub-1"]


async def test_creating_an_account_is_unavailable_when_keycloak_cannot_answer(client, session, keycloak_users):
    keycloak_users["error"]= KeycloakUnavailable("em baixo")

    response= await client.get("/me", headers=bearer(make_token()))

    assert response.status_code== 503
    assert await count_users(session)== 0


@pytest.mark.parametrize("status,expected", [(200, True), (404, False)])
async def test_keycloak_user_lookup(monkeypatch, status, expected):
    fake_keycloak(monkeypatch, delete_status=status)

    assert await keycloak_admin.user_exists("sub-1")== expected


async def test_keycloak_user_lookup_errors_are_reported_as_unavailable(monkeypatch):
    fake_keycloak(monkeypatch, delete_status=500)

    with pytest.raises(KeycloakUnavailable):
        await keycloak_admin.user_exists("sub-1")
