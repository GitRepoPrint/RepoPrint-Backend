import asyncio
from collections import Counter
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.schemas.dfd import DfdGraph
from app.services.code_fetcher import download_archive, read_archive
from app.services.dfd import build_dfd, parse_files
from app.services.dfd_schema import to_schema
from app.services.github import GitHubError, parse_repo_url

router= APIRouter(prefix="/dfd", tags=["dfd"])


def _analyse(archive, group_by: str) -> dict:
    files= read_archive(archive)
    parsed, failed= parse_files(files)
    graph= build_dfd(parsed, group_by)
    graph["stats"]= {
        "files_parsed": len(parsed),
        "files_with_syntax_errors": sum(p.has_errors for p in parsed),
        "routes": sum(len(p.routes) for p in parsed),
        "flows": len(graph["flows"]),
        "languages": dict(Counter(p.language for p in parsed)),
        "unsupported_languages": failed,
    }
    return graph


@router.get("", response_model=DfdGraph)
async def get_dfd(
    url: str= Query(..., description="URL do repositório GitHub público"),
    group_by: Literal["directory", "file"]= Query("directory", description="Granularidade dos processos"),
):
    try:
        owner, repo= parse_repo_url(url)
        archive= await download_archive(owner, repo)
        try:
            graph= await asyncio.to_thread(_analyse, archive, group_by)
        finally:
            archive.close()
        return to_schema(graph, owner, repo, group_by)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Falha ao contactar o GitHub")
