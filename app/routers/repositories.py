import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import Repository, User
from app.schemas.repository import RepositoryIn, RepositoryOut
from app.services import audit, github_oauth
from app.services.auth import get_current_user
from app.services.github import GitHubError, fetch_repository, parse_repo_url
from app.services.github_oauth import GitHubOAuthError

router= APIRouter(prefix="/repositories", tags=["repositories"])

RECONNECT= "A ligação ao GitHub deixou de ser válida. Liga a conta outra vez."
DUPLICATE= "Este repositório já está associado"


@router.get("", response_model=list[RepositoryOut])
async def list_repositories(user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    result= await session.scalars(select(Repository).where(Repository.user_id== user.id).order_by(Repository.created_at.desc(), Repository.id))
    return list(result)


@router.post("", response_model=RepositoryOut, status_code=201)
async def associate(body: RepositoryIn, user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    try:
        owner, repo= parse_repo_url(body.url)
    except GitHubError as error:
        raise HTTPException(error.status, error.message)
    token= None
    try:
        token= await github_oauth.user_token(session, user.id)
        info= await fetch_repository(owner, repo, token=token)
    except GitHubOAuthError as error:
        raise HTTPException(error.status, error.message)
    except GitHubError as error:
        if error.status== 401 and token:
            await github_oauth.drop_connection(session, user.id)
            raise HTTPException(409, RECONNECT)
        raise HTTPException(502 if error.status== 401 else error.status, error.message)
    except httpx.HTTPError:
        raise HTTPException(502, "Falha ao contactar o GitHub")

    url= f"https://github.com/{info['full_name']}".lower()
    existing= await session.scalar(select(Repository.id).where(Repository.user_id== user.id, Repository.url== url))
    if existing:
        raise HTTPException(409, DUPLICATE)
    repository= Repository(
        user_id=user.id,
        url=url,
        provider="github",
        name=info["full_name"],
        default_branch=info["default_branch"],
        is_private=info["private"],
    )
    session.add(repository)
    audit.record(session, "repository.associated", user_id=user.id, details={"repository": info["full_name"]})
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, DUPLICATE)
    await session.refresh(repository)
    return repository


@router.delete("/{repository_id}", status_code=204)
async def remove(repository_id: uuid.UUID, user: User= Depends(get_current_user), session: AsyncSession= Depends(get_session)):
    result= await session.execute(delete(Repository).where(Repository.id== repository_id, Repository.user_id== user.id))
    if result.rowcount== 0:
        raise HTTPException(404, "Repositório não encontrado")
    await session.commit()
    return Response(status_code=204)
