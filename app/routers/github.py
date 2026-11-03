import httpx
from fastapi import APIRouter, HTTPException, Query
from app.schemas.activity import ActivityMetrics
from app.services.github import GitHubError, fetch_activity, parse_repo_url

router= APIRouter(prefix="/github", tags=["github"])


@router.get("/activity", response_model=ActivityMetrics)
async def get_activity(url: str= Query(..., description="URL do repositório GitHub")):
    try:
        owner, repo= parse_repo_url(url)
        return await fetch_activity(owner, repo)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Falha ao contactar o GitHub")