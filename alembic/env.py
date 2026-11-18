import asyncio
import logging

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models
from app.config import settings
from app.db.base import Base

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

target_metadata= Base.metadata


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine= create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("As migrações só correm com ligação à base de dados.")

asyncio.run(run_async_migrations())
