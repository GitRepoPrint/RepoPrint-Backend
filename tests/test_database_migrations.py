import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

import app.models
from app.config import settings
from app.db.base import Base
from conftest import alembic_config

EXPECTED_TABLES= {
    "activity_metrics",
    "analysis_runs",
    "audit_logs",
    "chat_conversations",
    "chat_messages",
    "dfd_graphs",
    "false_positive_feedbacks",
    "repositories",
    "team_members",
    "teams",
    "threat_findings",
    "users",
    "users_oauth",
}


def inspect_database(callback):
    async def run():
        engine= create_async_engine(settings.database_url)
        async with engine.connect() as connection:
            result= await connection.run_sync(callback)
        await engine.dispose()
        return result

    return asyncio.run(run())


def table_names():
    return inspect_database(lambda connection: set(inspect(connection).get_table_names()) - {"alembic_version"})


def schema_differences():
    def compare(connection):
        context= MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    return inspect_database(compare)


@pytest.fixture
def clean_database(database):
    yield database
    command.downgrade(database, "base")
    command.upgrade(database, "head")


def test_there_is_a_single_migration_head():
    heads= ScriptDirectory.from_config(alembic_config()).get_heads()

    assert len(heads)== 1, f"Várias heads: {heads}. Junta-as com `alembic merge heads`."


def test_upgrade_creates_every_table(clean_database):
    command.downgrade(clean_database, "base")
    command.upgrade(clean_database, "head")

    assert table_names()== EXPECTED_TABLES


def test_downgrade_removes_every_table(clean_database):
    command.downgrade(clean_database, "base")

    assert table_names()== set()


def test_models_and_migrations_describe_the_same_schema(clean_database):
    assert schema_differences()== [], "Os modelos mudaram sem migração: corre `alembic revision --autogenerate`."
