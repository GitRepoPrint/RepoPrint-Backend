from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import User
from app.schemas.activity import ActivityMetrics
from app.services import audit, github_oauth, keycloak_admin
from app.services.auth import current_claims, find_or_create_user, get_current_user
from app.services.github import GitHubError, get_activity as load_activity, parse_repo_url
from app.services.github_oauth import GitHubOAuthError

router= APIRouter(prefix="/github", tags=["github"])

OUTCOMES= {404: "not_found", 429: "rate_limited"}
RECONNECT= "A ligação ao GitHub deixou de ser válida. Liga a conta outra vez."


class CallbackIn(BaseModel):
    code: str
    state: str


class SigninFinishIn(BaseModel):
    intent: Literal["login", "signup"]


def oauth_error(error: GitHubOAuthError) -> HTTPException:
    return HTTPException(error.status, error.message)


@router.post("/signin/finish")
async def finish_signin(body: SigninFinishIn, claims: dict= Depends(current_claims), session: AsyncSession= Depends(get_session)):
    user= await session.scalar(select(User).where(User.keycloak_sub== claims["sub"]))
    created= user is None
    if created and body.intent== "login":
        try:
            await keycloak_admin.delete_user(claims["sub"])
        except keycloak_admin.KeycloakUnavailable:
            raise HTTPException(503, "Serviço de autenticação indisponível")
        raise HTTPException(404, "Não existe conta ligada a este GitHub")
    if created:
        user= await find_or_create_user(claims, session)
    link= await github_oauth.connection(session, user.id)
    return {"created": created, "connected": link is not None}


@router.get("/connect")
async def connect(user: User= Depends(get_current_user)):
    try:
        return {"url": github_oauth.authorize_url(user.keycloak_sub)}
    except GitHubOAuthError as error:
        raise oauth_error(error)


@router.post("/callback")
async def callback(body: CallbackIn, user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    try:
        return {"login": await github_oauth.connect(session, user, body.code, body.state)}
    except GitHubOAuthError as error:
        raise oauth_error(error)


@router.get("/status")
async def status(user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    link= await github_oauth.connection(session, user.id)
    return {"connected": link is not None, "login": link.provider_username if link else None}


@router.delete("/connection", status_code=204)
async def disconnect(user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    try:
        await github_oauth.disconnect(session, user)
    except GitHubOAuthError as error:
        raise oauth_error(error)
    return Response(status_code=204)


@router.get("/activity", response_model=ActivityMetrics)
async def get_activity(
    url: str= Query(..., description="URL do repositório GitHub"),
    user: User= Depends(get_current_user),
    session: AsyncSession= Depends(get_session),
):
    try:
        owner, repo= parse_repo_url(url)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)

    outcome= "error"
    token= None
    try:
        token= await github_oauth.user_token(session, user.id)
        data= await load_activity(owner, repo, token=token, scope=str(user.id))
        outcome= "ok"
        return data
    except GitHubOAuthError as e:
        raise oauth_error(e)
    except GitHubError as e:
        outcome= OUTCOMES.get(e.status, "error")
        if e.status== 401 and token:
            await github_oauth.drop_connection(session, user.id)
            raise HTTPException(status_code=409, detail=RECONNECT)
        raise HTTPException(status_code=502 if e.status== 401 else e.status, detail=e.message)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Falha ao contactar o GitHub")
    finally:
        audit.record(session, "analysis.run", user_id=user.id, details={"repository": f"{owner}/{repo}", "outcome": outcome})
        await session.commit()
