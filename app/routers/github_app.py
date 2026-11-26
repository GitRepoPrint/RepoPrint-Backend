from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import User
from app.services import github_app
from app.services.auth import get_current_user
from app.services.github_app import GitHubAppError

router= APIRouter(prefix="/github/app", tags=["github-app"])


class AppCallbackIn(BaseModel):
    code: str
    installation_id: int
    state: str


def app_error(error: GitHubAppError) -> HTTPException:
    return HTTPException(error.status, error.message)


@router.get("/install")
async def install(user: User= Depends(get_current_user)):
    try:
        return {"url": github_app.install_url(user.keycloak_sub)}
    except GitHubAppError as error:
        raise app_error(error)


@router.post("/callback")
async def callback(body: AppCallbackIn, user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    try:
        return {"login": await github_app.complete_install(session, user, body.code, body.installation_id, body.state)}
    except GitHubAppError as error:
        raise app_error(error)


@router.get("/status")
async def status(user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    rows= await github_app.installations(session, user)
    return {
        "configured": github_app.configured(),
        "installations": [
            {
                "installation_id": row.installation_id,
                "account_login": row.account_login,
                "account_type": row.account_type,
                "repository_selection": row.repository_selection,
                "failing": row.failed_since is not None,
            }
            for row in rows
        ],
    }


@router.delete("/installations/{installation_id}", status_code=204)
async def remove(installation_id: int, user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    if not await github_app.remove(session, user, installation_id):
        raise HTTPException(404, "Instalação não encontrada")
    return Response(status_code=204)
