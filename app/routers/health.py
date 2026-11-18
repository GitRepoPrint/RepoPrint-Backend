from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

router= APIRouter(prefix="/health", tags=["health"])


@router.get("/db")
async def database_health(session: AsyncSession= Depends(get_session)):
    try:
        await session.execute(select(1))
    except (SQLAlchemyError, OSError):
        raise HTTPException(status_code=503, detail="Base de dados indisponível")
    return {"status": "ok"}
