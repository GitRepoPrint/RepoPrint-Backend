import pytest

from test_auth import bearer, client, keycloak, make_token
from test_github_app import INSTALL_TOKEN, URL, app_env, fake, headers, make_user

pytestmark= pytest.mark.anyio

BIA= {"sub": "sub-2", "email": "bia@example.com"}


async def source(client, url=URL, **token_args):
    return await client.get("/dfd/source", params={"url": url}, headers=headers(**token_args))


async def test_one_download_gives_the_config_files_and_nothing_else(client, session, fake):
    await make_user(session)

    response= await source(client)

    assert response.status_code== 200
    body= response.json()
    assert body["files"]== {
        "docker-compose.yml": "services: {}\n",
        "src/Dockerfile": "FROM python\n",
        "package.json": '{"name": "x"}\n',
    }
    assert [(e["path"], e["type"]) for e in body["tree"]]== [("docker-compose.yml", "blob"), ("src/Dockerfile", "blob"), ("package.json", "blob")]
    assert body["truncated"] is False
    assert fake.tarball_calls== [INSTALL_TOKEN]


async def test_the_same_repository_is_not_downloaded_twice_for_the_same_user(client, session, fake):
    await make_user(session)

    first= await source(client)
    second= await source(client)

    assert first.json()== second.json()
    assert len(fake.tarball_calls)== 1


async def test_the_cache_is_never_shared_with_someone_else(client, session, fake):
    await make_user(session)
    await make_user(session, **BIA, oauth=False)
    assert (await source(client)).status_code== 200

    other= await source(client, **BIA)

    assert other.status_code== 404
    assert isinstance(other.json()["detail"], str)


async def test_a_private_repository_without_the_app_asks_to_install_it(client, session, fake):
    await make_user(session)
    fake.on_repo.clear()

    response= await source(client)

    assert response.status_code== 404
    assert response.json()["detail"]["code"]== "app_not_installed"
    assert fake.tarball_calls== []


async def test_too_many_candidates_are_cut_and_flagged(client, session, fake):
    await make_user(session)
    fake.archive_files= {f"svc{i}/Dockerfile": "FROM python\n" for i in range(600)}

    body= (await source(client)).json()

    assert len(body["files"])== 500 and len(body["tree"])== 500
    assert body["truncated"] is True


async def test_it_needs_a_session(client, fake):
    assert (await client.get("/dfd/source", params={"url": URL})).status_code== 401


async def test_a_bad_address_is_a_400(client, session, fake):
    await make_user(session)

    assert (await source(client, url="https://gitlab.com/o/r")).status_code== 400
