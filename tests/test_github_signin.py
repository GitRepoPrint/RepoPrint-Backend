import pytest
from sqlalchemy import func, select

from app.models import AuditLog, User
from app.services import keycloak_admin
from app.services.keycloak_admin import KeycloakUnavailable

from test_auth import bearer, client, keycloak, make_token
from test_github_oauth import connect_as, github_app, hub

pytestmark= pytest.mark.anyio


@pytest.fixture
def deleted(monkeypatch):
    state= {"subs": [], "error": None}

    async def delete_user(sub):
        state["subs"].append(sub)
        if state["error"]:
            raise state["error"]

    monkeypatch.setattr(keycloak_admin, "delete_user", delete_user)
    return state


async def finish(client, intent, **token_args):
    return await client.post("/github/signin/finish", json={"intent": intent}, headers=bearer(make_token(**token_args)))


async def count_users(session):
    return await session.scalar(select(func.count()).select_from(User))


async def test_signup_creates_the_account_row_and_reports_it_is_not_connected_yet(client, session, deleted):
    response= await finish(client, "signup")

    assert response.status_code== 200
    assert response.json()== {"created": True, "connected": False}
    assert await count_users(session)== 1
    assert deleted["subs"]== []
    assert "account.created" in list(await session.scalars(select(AuditLog.action)))


async def test_login_of_an_account_that_does_not_exist_removes_the_keycloak_user(client, session, deleted):
    response= await finish(client, "login")

    assert response.status_code== 404
    assert deleted["subs"]== ["sub-1"]
    assert await count_users(session)== 0


async def test_login_of_an_existing_account_does_not_remove_anything(client, session, deleted):
    await client.get("/me", headers=bearer(make_token()))

    response= await finish(client, "login")

    assert response.json()== {"created": False, "connected": False}
    assert deleted["subs"]== [] and await count_users(session)== 1


async def test_signup_of_an_existing_account_behaves_like_a_login(client, session, deleted):
    await client.get("/me", headers=bearer(make_token()))

    response= await finish(client, "signup")

    assert response.json()== {"created": False, "connected": False}
    assert await count_users(session)== 1


async def test_it_reports_when_the_github_token_is_already_stored(client, session, hub, deleted):
    await finish(client, "signup")
    await connect_as(client, hub, "code-ana")

    response= await finish(client, "login")

    assert response.json()== {"created": False, "connected": True}


async def test_a_keycloak_failure_while_refusing_gives_503(client, session, deleted):
    deleted["error"]= KeycloakUnavailable("em baixo")

    response= await finish(client, "login")

    assert response.status_code== 503


async def test_it_requires_a_token_and_a_valid_intent(client, deleted):
    without= await client.post("/github/signin/finish", json={"intent": "login"})
    invalid= await client.post("/github/signin/finish", json={"intent": "outra"}, headers=bearer(make_token()))

    assert without.status_code== 401 and invalid.status_code== 422
