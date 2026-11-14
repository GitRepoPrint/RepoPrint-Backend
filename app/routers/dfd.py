import asyncio
from collections import Counter
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

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


async def _analyse_repository(owner: str, repo: str, group_by: str) -> dict:
    archive= await download_archive(owner, repo)
    try:
        graph= await asyncio.to_thread(_analyse, archive, group_by)
    finally:
        archive.close()
    return to_schema(graph, owner, repo, group_by)


@router.get("", response_model=DfdGraph)
async def get_dfd(
    request: Request,
    url: str= Query(..., description="URL do repositório GitHub público"),
    group_by: Literal["directory", "file"]= Query("directory", description="Granularidade dos processos"),
):
    guard= request.app.state.guard
    try:
        owner, repo= parse_repo_url(url)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)

    # O que já se analisou há pouco sai da cache: não custa nada, por isso não conta para o limite de ninguém
    key= (owner.lower(), repo.lower(), group_by)
    cached= guard.cache.get(key)
    if cached is not None:
        return cached

    wait= guard.limiter.check(guard.client_id(request.client.host if request.client else None, request.headers.get("x-forwarded-for")))
    if wait > 0:
        raise HTTPException(status_code=429, detail="Demasiados pedidos: tente mais tarde", headers={"Retry-After": str(int(wait) + 1)})
    if not await guard.acquire_slot():
        raise HTTPException(status_code=503, detail="Servidor ocupado: tente mais tarde", headers={"Retry-After": "10"})
    try:
        result= await _analyse_repository(owner, repo, group_by)
        guard.cache.set(key, result)
        return result
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Falha ao contactar o GitHub")
    finally:
        guard.slots.release()
