from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import User
from app.schemas.user import UserOut
from app.services import account, audit
from app.services.auth import current_claims, get_current_user
from app.services.keycloak_admin import KeycloakUnavailable

router= APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
async def me(user: User= Depends(get_current_user)):
    return user


@router.get("/me/export")
async def export_me(user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    data= await account.export_user_data(session, user)
    audit.record(session, "account.exported", user_id=user.id)
    await session.commit()
    return JSONResponse(
        jsonable_encoder(data),
        headers={"Content-Disposition": 'attachment; filename="repoprint-data.json"'},
    )


@router.delete("/me", status_code=204)
async def delete_me(claims: dict= Depends(current_claims), session: AsyncSession= Depends(get_session)):
    try:
        await account.delete_account(session, claims["sub"])
    except KeycloakUnavailable:
        raise HTTPException(503, "Os dados foram apagados, mas a conta de login não. Tenta outra vez.")
    return Response(status_code=204)
