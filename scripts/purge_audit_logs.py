import asyncio

from app.db.session import get_engine, get_sessionmaker
from app.services.audit import RETENTION_DAYS, purge_older_than


async def main() -> None:
    async with get_sessionmaker()() as session:
        removed= await purge_older_than(session)
    await get_engine().dispose()
    print(f"{removed} registos de auditoria com mais de {RETENTION_DAYS} dias apagados")


if __name__ == "__main__":
    asyncio.run(main())
