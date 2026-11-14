import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.services import github
from app.services.auth import current_claims
from main import app

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


@pytest.fixture
def signed_in():
    app.dependency_overrides[current_claims]= lambda: {"sub": "user-1"}
    yield
    app.dependency_overrides.pop(current_claims, None)


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


def pull(hours, *, merged=True):
    created= datetime(2026, 10, 1, tzinfo=timezone.utc)
    done= (created + timedelta(hours=hours)).isoformat()
    return {"created_at": created.isoformat(), "merged_at": done if merged else None}


def count_response(count):
    items= [{}] if count else []
    headers= {"Link": f'<https://api.github.com/x?per_page=1&page={count}>; rel="last"'} if count > 1 else {}
    return httpx.Response(200, json=items, headers=headers)


class FakeGitHub:
    def __init__(self, *, total=1200, last_30d=40, last_90d=300, sample=(), closed=(), open_prs=3, open_issues_count=10, pulls=True, empty=False, missing=False, limited=False):
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

        if self.limited:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        if self.missing:
            return httpx.Response(404)
        if path.endswith("/commits"):
            return self._commits(params)
        if path.endswith("/pulls"):
            return self._pulls(params)
        return httpx.Response(200, json={
            "default_branch": "main",
            "open_issues_count": self.open_issues_count,
            "stargazers_count": 7,
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
