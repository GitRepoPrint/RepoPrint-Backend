import uuid
from datetime import timedelta

from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

RETENTION_DAYS= 90


def record(session: AsyncSession, action: str, *, user_id: uuid.UUID | None= None, details: dict | None= None) -> None:
    session.add(AuditLog(action=action, user_id=user_id, details=details))


async def purge_older_than(session: AsyncSession, days: int= RETENTION_DAYS) -> int:
    result= await session.execute(delete(AuditLog).where(AuditLog.timestamp < func.now() - timedelta(days=days)))
    await session.commit()
    return result.rowcount
