"""Endpoints da API para análise de ameaças de privacidade com LINDDUN."""
from typing import Any, Dict, Literal, Union

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from app.routers.dfd import _admit, _analyse_repository, _failure
from app.schemas.dfd import DfdGraph
from app.engines.linddun.schemas import LinddunReport
from app.services.github import GitHubError
from app.engines.linddun.catalog import load_catalog
from app.engines.linddun.engine import LinddunEngine

router = APIRouter(prefix="/linddun", tags=["linddun"])


@router.get("", response_model=LinddunReport)
async def analyze_repository_privacy(
    request: Request,
    url: str = Query(..., description="URL do repositório GitHub público"),
    group_by: Literal["directory", "file"] = Query("directory", description="Granularidade dos processos"),
):
    """Gera o DFD do repositório GitHub e corre a análise de ameaças LINDDUN."""
    admission = await _admit(request, url, group_by)
    engine = LinddunEngine()

    # Se o DFD já estiver em cache, reaproveita diretamente sem novo download
    if admission.cached is not None:
        return engine.analyze(admission.cached)

    try:
        dfd_graph = await _analyse_repository(admission.owner, admission.repo, group_by)
        request.app.state.guard.cache.set(admission.key, dfd_graph)
        return engine.analyze(dfd_graph)
    except (GitHubError, httpx.HTTPError) as error:
        status, detail = _failure(error)
        raise HTTPException(status_code=status, detail=detail)
    finally:
        if admission.lease:
            admission.lease.release()


@router.post("/analyze", response_model=LinddunReport)
async def analyze_custom_dfd(dfd: Union[DfdGraph, Dict[str, Any]]):
    """Analisa diretamente um grafo DFD fornecido no corpo do pedido (JSON)."""
    engine = LinddunEngine()
    return engine.analyze(dfd)


@router.get("/catalog")
async def get_linddun_catalog():
    """Retorna o catálogo completo de categorias, regras e propriedades LINDDUN."""
    return load_catalog()
