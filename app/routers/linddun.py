"""Endpoints da API para análise de ameaças de privacidade com LINDDUN."""
from typing import Any, Dict, Literal, Union

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from app.routers.dfd import _analyse_repository
from app.schemas.dfd import DfdGraph
from app.schemas.linddun import LinddunReport
from app.services.github import GitHubError, parse_repo_url
from app.services.linddun_engine import LinddunEngine

router = APIRouter(prefix="/linddun", tags=["linddun"])


@router.get("", response_model=LinddunReport)
async def analyze_repository_privacy(
    request: Request,
    url: str = Query(..., description="URL do repositório GitHub público"),
    group_by: Literal["directory", "file"] = Query("directory", description="Granularidade dos processos"),
):
    """Gera o DFD do repositório GitHub e corre a análise de ameaças LINDDUN."""
    guard = request.app.state.guard
    try:
        owner, repo = parse_repo_url(url)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)

    key = (owner.lower(), repo.lower(), group_by)
    cached_dfd = guard.cache.get(key)
    engine = LinddunEngine()

    # Se o DFD já estiver em cache, reaproveita diretamente sem novo download
    if cached_dfd is not None:
        return engine.analyze(cached_dfd)

    wait = guard.limiter.check(
        guard.client_id(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
        )
    )
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail="Demasiados pedidos: tente mais tarde",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    if not await guard.acquire_slot():
        raise HTTPException(
            status_code=503,
            detail="Servidor ocupado: tente mais tarde",
            headers={"Retry-After": "10"},
        )

    try:
        dfd_graph = await _analyse_repository(owner, repo, group_by)
        guard.cache.set(key, dfd_graph)
        return engine.analyze(dfd_graph)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Falha ao contactar o GitHub")
    finally:
        guard.slots.release()


@router.post("/analyze", response_model=LinddunReport)
async def analyze_custom_dfd(dfd: Union[DfdGraph, Dict[str, Any]]):
    """Analisa diretamente um grafo DFD fornecido no corpo do pedido (JSON)."""
    engine = LinddunEngine()
    return engine.analyze(dfd)
