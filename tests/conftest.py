import asyncio
import base64
import itertools
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.db.session import get_session
from app.models import User
from app.services import github
from app.services.auth import get_current_user
from main import app

@pytest.fixture(autouse=True)
def archive_only_fetching(monkeypatch):
    """Nenhum teste fala com o GitHub: os do GET /dfd trocam o arquivo por um de teste (`dfd_router.download_archive`);
    os do descarregamento ficheiro a ficheiro (tests/test_code_fetcher.py) pedem-no de propósito."""
    monkeypatch.setattr(settings, "dfd_fetch_method", "archive")


ALEMBIC_INI= Path(__file__).resolve().parent.parent / "alembic.ini"


def alembic_config() -> Config:
    config= Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return config


async def can_connect() -> bool:
    engine= create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database():
    if not settings.db_name.endswith("_test"):
        pytest.skip("Os testes da BD apagam tudo: define DB_NAME com um nome terminado em _test.")
    if not asyncio.run(can_connect()):
        pytest.skip("Postgres indisponível: define DB_HOST, DB_PORT, DB_NAME, DB_USER e DB_PASSWORD.")
    config= alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield config
    command.downgrade(config, "base")


@pytest.fixture
async def session(database):
    engine= create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        transaction= await connection.begin()
        db_session= AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield db_session
        finally:
            await db_session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeSession:
    def add(self, *args):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def scalar(self, *args, **kwargs):
        return None


@pytest.fixture
def no_database():
    async def fake_session():
        yield FakeSession()

    app.dependency_overrides[get_session]= fake_session
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def signed_in(no_database):
    user= User(id=uuid.uuid4(), keycloak_sub="user-1", email="user@example.com")
    app.dependency_overrides[get_current_user]= lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def fresh_dfd_guard():
    """A aplicação dos testes é a mesma em todos: sem isto, o que um teste guardou na cache do /dfd aparecia no seguinte."""
    import main
    from app.config import settings
    from app.services.guard import DfdGuard

    main.app.state.guard= DfdGuard.from_settings(settings)
    yield


@pytest.fixture(autouse=True)
def clean_cache():
    github._cache.clear()
    yield
    github._cache.clear()


def commit(login, when, *, bot=False, email="anon@example.com"):
    author= {"login": login, "type": "Bot" if bot else "User"} if login else None
    stamp= when.isoformat()
    return {
        "author": author,
        "commit": {"author": {"email": email, "date": stamp}, "committer": {"date": stamp}},
    }


PULL_NUMBERS= itertools.count(1)


def pull(hours, *, merged=True, day=1):
    created= datetime(2026, 10, day, tzinfo=timezone.utc)
    done= (created + timedelta(hours=hours)).isoformat()
    return {"number": next(PULL_NUMBERS), "created_at": created.isoformat(), "merged_at": done if merged else None}


def count_response(count):
    items= [{"sha": "a3f89c1d" * 5}] if count else []
    headers= {"Link": f'<https://api.github.com/x?per_page=1&page={count}>; rel="last"'} if count > 1 else {}
    return httpx.Response(200, json=items, headers=headers)


class FakeGitHub:
    def __init__(self, *, total=1200, last_30d=40, last_90d=300, sample=(), closed=(), open_prs=3, open_issues_count=10, pulls=True, empty=False, missing=False, limited=False, private=False, unauthorized=False, canonical=None, branches=None, ci="success", license_="MIT", actions_forbidden=False, files=None, codeowners=None, codeowners_path=".github/CODEOWNERS", tree_truncated=False, tree_missing=False, reviews=None, graphql_status=200, graphql_errors=False):
        self.canonical= canonical
        self.branches= [{"name": "main", "protected": True}, {"name": "develop", "protected": False}] if branches is None else branches
        self.ci= ci
        self.license_= license_
        self.actions_forbidden= actions_forbidden
        self.files= ["README.md", "src/app.py"] if files is None else list(files)
        self.codeowners= codeowners
        self.codeowners_path= codeowners_path
        self.tree_truncated= tree_truncated
        self.tree_missing= tree_missing
        self.reviews= reviews
        self.graphql_status= graphql_status
        self.graphql_errors= graphql_errors
        self.private= private
        self.unauthorized= unauthorized
        self.total= total
        self.last_30d= last_30d
        self.last_90d= last_90d
        self.sample= list(sample)
        self.closed= list(closed)
        self.open_prs= open_prs
        self.open_issues_count= open_issues_count
        self.pulls= pulls
        self.empty= empty
        self.missing= missing
        self.limited= limited
        self.calls= []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path= request.url.path
        params= dict(request.url.params)

        if self.unauthorized:
            return httpx.Response(401)
        if self.limited:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        if self.missing:
            return httpx.Response(404)
        if path.endswith("/commits"):
            return self._commits(params)
        if path.endswith("/pulls"):
            return self._pulls(params)
        if path.endswith("/graphql"):
            return self._graphql()
        if "/git/trees/" in path:
            return self._tree()
        if "/contents/" in path:
            return self._contents(path)
        if path.endswith("/branches"):
            return self._branches(params)
        if path.endswith("/actions/runs"):
            return self._runs()
        return httpx.Response(200, json={
            "license": {"spdx_id": self.license_, "name": f"{self.license_} License"} if self.license_ else None,
            "default_branch": "main",
            "open_issues_count": self.open_issues_count,
            "stargazers_count": 7,
            "private": self.private,
            "full_name": self.canonical or path.removeprefix("/repos/"),
        })

    def _commits(self, params):
        if self.empty:
            return httpx.Response(409)
        if params.get("per_page")== "1":
            if "since" not in params:
                return count_response(self.total)
            since= datetime.fromisoformat(params["since"])
            recent= datetime.now(timezone.utc) - since < timedelta(days=60)
            return count_response(self.last_30d if recent else self.last_90d)
        page= int(params.get("page", 1))
        chunk= self.sample[(page - 1) * 100 : page * 100]
        headers= {}
        if page * 100 < len(self.sample):
            headers["Link"]= f'<https://api.github.com/repos/o/r/commits?per_page=100&page={page + 1}>; rel="next"'
        return httpx.Response(200, json=chunk, headers=headers)

    def _graphql(self):
        if self.graphql_status != 200:
            return httpx.Response(self.graphql_status)
        if self.graphql_errors:
            return httpx.Response(200, json={"errors": [{"message": "boom"}]})
        nodes= [
            {"author": {"login": pr["author"]} if pr["author"] else None, "reviews": {"nodes": [{"author": {"login": name}} for name in pr["reviewers"]]}}
            for pr in (self.reviews or [])
        ]
        return httpx.Response(200, json={"data": {"repository": {"pullRequests": {"nodes": nodes}}}})

    def _tree(self):
        if self.tree_missing:
            return httpx.Response(404)
        paths= list(self.files)
        if self.codeowners is not None and self.codeowners_path not in paths:
            paths.append(self.codeowners_path)
        return httpx.Response(200, json={"truncated": self.tree_truncated, "tree": [{"path": p, "type": "blob"} for p in paths] + [{"path": "src", "type": "tree"}]})

    def _contents(self, path):
        if self.codeowners is None or not path.endswith("/" + self.codeowners_path):
            return httpx.Response(404)
        encoded= base64.b64encode(self.codeowners.encode()).decode()
        return httpx.Response(200, json={"encoding": "base64", "content": encoded})

    def _branches(self, params):
        items= [b for b in self.branches if b["protected"]] if params.get("protected")== "true" else self.branches
        if params.get("per_page")== "1":
            return count_response(len(items))
        page= items[:100]
        headers= {"Link": '<https://api.github.com/repos/o/r/branches?per_page=100&page=2>; rel="next"'} if len(items) > 100 else {}
        return httpx.Response(200, json=page, headers=headers)

    def _runs(self):
        if self.actions_forbidden:
            return httpx.Response(403)
        if self.ci== "missing":
            return httpx.Response(404)
        runs= [{"conclusion": self.ci, "updated_at": "2026-10-20T10:00:00Z"}] if self.ci else []
        return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": runs})

    def _pulls(self, params):
        if not self.pulls:
            return httpx.Response(404)
        if params.get("state")== "open":
            return count_response(self.open_prs)
        return httpx.Response(200, json=self.closed)


@pytest.fixture
def install(monkeypatch):
    real= httpx.AsyncClient

    def apply(fake: FakeGitHub) -> FakeGitHub:
        monkeypatch.setattr(
            github.httpx,
            "AsyncClient",
            lambda **kwargs: real(transport=httpx.MockTransport(fake.handler), **kwargs),
        )
        return fake

    return apply
