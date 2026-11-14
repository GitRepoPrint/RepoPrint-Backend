from fastapi import APIRouter, Depends

from app.models import User
from app.schemas.user import UserOut
from app.services.auth import get_current_user

router= APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
async def me(user: User= Depends(get_current_user)):
    return user
