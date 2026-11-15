from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import AuditLog, Repository, User
from app.services import account, github

from conftest import FakeGitHub
from test_auth import bearer, client, keycloak, make_token
from test_github_oauth import audit_actions, connect_as, hub, github_app, stored

pytestmark= pytest.mark.anyio

BIA= {"sub": "sub-2", "email": "bia@example.com"}


def headers(**token_args):
    return bearer(make_token(**token_args))


async def associate(client, url="https://github.com/o/r", **token_args):
    return await client.post("/repositories", json={"url": url}, headers=headers(**token_args))


async def count(session):
    return await session.scalar(select(func.count()).select_from(Repository))


async def test_the_list_starts_empty(client):
    response= await client.get("/repositories", headers=headers())

    assert response.status_code== 200 and response.json()== []


async def test_associating_stores_the_canonical_name_and_a_normalized_url(client, session, hub):
    hub.repos.canonical= "Octo/Repo-Name"
    hub.repos.private= False

    response= await associate(client, "https://www.github.com/OCTO/repo-name.git/")

    body= response.json()
    assert response.status_code== 201
    assert body["name"]== "Octo/Repo-Name"
    assert body["url"]== "https://github.com/octo/repo-name"
    assert (body["provider"], body["is_private"], body["default_branch"])== ("github", False, "main")
    assert body["id"] and body["created_at"]
    assert (await client.get("/repositories", headers=headers())).json()== [body]


async def test_a_duplicate_with_other_capitalization_is_a_conflict(client, session, hub):
    hub.repos.canonical= "Octo/Repo"
    await associate(client, "https://github.com/octo/repo")

    response= await associate(client, "https://github.com/OCTO/REPO/tree/main")

    assert response.status_code== 409
    assert await count(session)== 1


async def test_two_users_can_associate_the_same_repository(client, session, hub):
    first= await associate(client)
    second= await associate(client, **BIA)

    assert (first.status_code, second.status_code)== (201, 201)
    assert await count(session)== 2


async def test_a_private_repository_needs_a_connected_github_account(client, session, hub):
    hub.repos.private= True

    without= await associate(client)
    await connect_as(client, hub, "code-ana")
    with_link= await associate(client)

    assert without.status_code== 404 and await count(session)== 1
    assert with_link.status_code== 201 and with_link.json()["is_private"] is True
    assert "Bearer gho_ana" in {request.headers.get("authorization") for request in hub.repos.calls}


@pytest.mark.parametrize("url", ["https://gitlab.com/o/r", "isto nao e um url", "https://github.com/so-owner"])
async def test_an_invalid_url_is_rejected(client, session, hub, url):
    response= await associate(client, url)

    assert response.status_code== 400 and await count(session)== 0
    assert hub.repos.calls== []


@pytest.mark.parametrize("flag, status", [("missing", 404), ("limited", 429)])
async def test_github_errors_pass_through_without_storing(client, session, hub, flag, status):
    setattr(hub.repos, flag, True)

    response= await associate(client)

    assert response.status_code== status and await count(session)== 0


async def test_a_broken_github_gives_502(client, session, monkeypatch):
    class Down:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(github.httpx, "AsyncClient", Down)

    response= await associate(client)

    assert response.status_code== 502 and await count(session)== 0


async def test_a_rejected_github_token_drops_the_link_and_asks_to_reconnect(client, session, hub):
    await connect_as(client, hub, "code-ana")
    hub.repos.unauthorized= True

    response= await associate(client)

    assert response.status_code== 409
    assert "Liga a conta outra vez" in response.json()["detail"]
    assert await stored(session)== [] and await count(session)== 0
    assert "github.connection_lost" in await audit_actions(session)


async def test_a_server_token_never_exposes_a_private_repository(client, session, hub, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "server-token")
    hub.repos.private= True

    response= await associate(client)

    assert response.status_code== 404


async def test_each_user_only_sees_their_own_repositories(client, session, hub):
    await associate(client, "https://github.com/o/mine")
    await associate(client, "https://github.com/o/hers", **BIA)

    mine= (await client.get("/repositories", headers=headers())).json()
    hers= (await client.get("/repositories", headers=headers(**BIA))).json()

    assert [item["name"] for item in mine]== ["o/mine"]
    assert [item["name"] for item in hers]== ["o/hers"]


async def test_the_list_has_the_newest_first(client, session, hub):
    await associate(client, "https://github.com/o/old")
    await associate(client, "https://github.com/o/new")
    now= datetime.now(timezone.utc)
    for name, age in (("o/old", 2), ("o/new", 1)):
        row= await session.scalar(select(Repository).where(Repository.name== name))
        row.created_at= now - timedelta(days=age) if name== "o/old" else now
    await session.flush()

    names= [item["name"] for item in (await client.get("/repositories", headers=headers())).json()]

    assert names== ["o/new", "o/old"]


async def test_removing_deletes_the_repository(client, session, hub):
    created= (await associate(client)).json()

    response= await client.delete(f"/repositories/{created['id']}", headers=headers())

    assert response.status_code== 204 and await count(session)== 0
    assert (await client.get("/repositories", headers=headers())).json()== []


async def test_removing_someone_elses_repository_is_a_404(client, session, hub):
    created= (await associate(client)).json()
    await client.get("/me", headers=headers(**BIA))

    other= await client.delete(f"/repositories/{created['id']}", headers=headers(**BIA))
    unknown= await client.delete("/repositories/00000000-0000-0000-0000-000000000000", headers=headers())

    assert (other.status_code, unknown.status_code)== (404, 404)
    assert await count(session)== 1


async def test_associating_is_audited_with_the_repository_name(client, session, hub):
    hub.repos.canonical= "Octo/Repo"

    await associate(client, "https://github.com/octo/repo")

    log= await session.scalar(select(AuditLog).where(AuditLog.action== "repository.associated"))
    assert log.details== {"repository": "Octo/Repo"} and log.user_id is not None


async def test_the_export_lists_the_associated_repositories(client, session, hub):
    await associate(client)

    body= (await client.get("/me/export", headers=headers())).json()

    assert [item["name"] for item in body["repositories"]]== ["o/r"]


async def test_deleting_the_account_removes_the_repositories(client, session, hub, monkeypatch):
    async def delete_user(sub):
        return None

    monkeypatch.setattr(account.keycloak_admin, "delete_user", delete_user)
    await associate(client)
    await associate(client, "https://github.com/o/two")

    response= await client.delete("/me", headers=headers())

    assert response.status_code== 204
    assert await count(session)== 0
    assert await session.scalar(select(func.count()).select_from(User))== 0


async def test_every_endpoint_needs_a_token(client):
    assert (await client.get("/repositories")).status_code== 401
    assert (await client.post("/repositories", json={"url": "https://github.com/o/r"})).status_code== 401
    assert (await client.delete("/repositories/00000000-0000-0000-0000-000000000000")).status_code== 401
