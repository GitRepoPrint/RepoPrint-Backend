import asyncio
import json
from collections import Counter, deque
from dataclasses import dataclass
from typing import Callable, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas.dfd import DfdGraph
from app.services.code_fetcher import FetchResult, download_archive, fetch_repository_files
from app.services.dfd import build_dfd, parse_files
from app.services.dfd_schema import to_schema
from app.services.github import GitHubError, parse_repo_url
from app.services.guard import Lease

router= APIRouter(prefix="/dfd", tags=["dfd"])

Progress= Callable[[str, int, int], None]


def _analyse(fetched: FetchResult, group_by: str, progress: Progress | None= None) -> dict:
    parsed, failed= parse_files(fetched.files, (lambda done, total: progress("parsing", done, total)) if progress else None)
    if progress:
        progress("building", 0, 0)
    graph= build_dfd(parsed, group_by)
    graph["stats"]= {
        "files_parsed": len(parsed),
        "files_with_syntax_errors": sum(p.has_errors for p in parsed),
        "routes": sum(len(p.routes) for p in parsed),
        "flows": len(graph["flows"]),
        "languages": dict(Counter(p.language for p in parsed)),
        "unsupported_languages": failed,
        "files_found": fetched.candidates,
        "sampled": fetched.sampled,
        "listing_truncated": fetched.truncated,
        "fetch_method": fetched.method,
    }
    return graph


async def _analyse_repository(owner: str, repo: str, group_by: str, progress: Progress | None= None) -> dict:
    # `download_archive` é o plano B (e o único método com dfd_fetch_method="archive"): vai-se buscar aqui ao módulo, a cada chamada
    fetched= await fetch_repository_files(owner, repo, progress=progress, archive_loader=lambda: download_archive(owner, repo))
    graph= await asyncio.to_thread(_analyse, fetched, group_by, progress)
    return to_schema(graph, owner, repo, group_by)


def _failure(error: Exception) -> tuple[int, str] | None:
    if isinstance(error, GitHubError):
        return error.status, error.message
    if isinstance(error, httpx.HTTPError):
        return 502, "Falha ao contactar o GitHub"
    return None


@dataclass
class _Admission:
    key: tuple
    owner: str
    repo: str
    group_by: str
    #: o resultado, se esta análise já se fez há pouco (e então não há vaga nem limite)
    cached: dict | None
    lease: Lease | None


async def _admit(request: Request, url: str, group_by: str) -> _Admission:
    """O que vem antes de analisar: o endereço, a cache, o limite por cliente e a vaga. Falha com o código HTTP certo."""
    guard= request.app.state.guard
    try:
        owner, repo= parse_repo_url(url)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message)

    # O que já se analisou há pouco sai da cache: não custa nada, por isso não conta para o limite de ninguém
    key= (owner.lower(), repo.lower(), group_by)
    cached= guard.cache.get(key)
    if cached is not None:
        return _Admission(key, owner, repo, group_by, cached, None)

    wait= guard.limiter.check(guard.client_id(request.client.host if request.client else None, request.headers.get("x-forwarded-for")))
    if wait > 0:
        raise HTTPException(status_code=429, detail="Demasiados pedidos: tente mais tarde", headers={"Retry-After": str(int(wait) + 1)})
    lease= await guard.lease()
    if lease is None:
        raise HTTPException(status_code=503, detail="Servidor ocupado: tente mais tarde", headers={"Retry-After": "10"})
    return _Admission(key, owner, repo, group_by, None, lease)


@router.get("", response_model=DfdGraph)
async def get_dfd(
    request: Request,
    url: str= Query(..., description="URL do repositório GitHub público"),
    group_by: Literal["directory", "file"]= Query("directory", description="Granularidade dos processos"),
):
    admission= await _admit(request, url, group_by)
    if admission.cached is not None:
        return admission.cached
    try:
        result= await _analyse_repository(admission.owner, admission.repo, group_by)
        request.app.state.guard.cache.set(admission.key, result)
        return result
    except (GitHubError, httpx.HTTPError) as error:
        status, detail= _failure(error)
        raise HTTPException(status_code=status, detail=detail)
    finally:
        admission.lease.release()


def _result_event(result: dict) -> dict:
    """O resultado como o GET /dfd o devolve: passa pelo mesmo modelo, que acrescenta os campos vazios (`file`, `line`, `span`)."""
    return {"type": "result", "graph": DfdGraph.model_validate(result).model_dump(mode="json")}


def _line(event: dict) -> str:
    return json.dumps(event, default=str, separators=(",", ":")) + "\n"


@router.get("/stream")
async def stream_dfd(
    request: Request,
    url: str= Query(..., description="URL do repositório GitHub público"),
    group_by: Literal["directory", "file"]= Query("directory", description="Granularidade dos processos"),
):
    """A mesma análise do GET /dfd, em NDJSON (uma linha de JSON por evento), para o interface mostrar o avanço a sério:

        {"type":"progress","stage":"listing|downloading|parsing|building","done":N,"total":M}   (total 0: não se sabe)
        {"type":"result","graph":{...}}                                                          (o fim: o mesmo grafo do GET /dfd)
        {"type":"error","status":404,"detail":"..."}                                             (o fim, se a análise falhou)

    O que se recusa antes de começar (endereço mau, limite por cliente, servidor ocupado) é um erro HTTP normal."""
    admission= await _admit(request, url, group_by)
    guard= request.app.state.guard

    if admission.cached is not None:
        async def cached():
            yield _line(_result_event(admission.cached))

        return StreamingResponse(cached(), media_type="application/x-ndjson")

    async def events():
        loop= asyncio.get_running_loop()
        # Os avisos de avanço vêm de threads (descarregar e analisar correm fora do ciclo de eventos). A thread acrescenta-os a esta
        # fila já, e só avisa o ciclo de eventos para acordar: assim a ordem não depende de quando o ciclo corre cada aviso (o
        # resultado nunca passa à frente de um aviso que a thread já deu).
        pending: deque= deque()
        wake= asyncio.Event()
        final: list[dict]= []

        def progress(stage: str, done: int, total: int) -> None:
            pending.append({"type": "progress", "stage": stage, "done": done, "total": total})
            loop.call_soon_threadsafe(wake.set)

        async def work():
            try:
                result= await _analyse_repository(admission.owner, admission.repo, group_by, progress)
                guard.cache.set(admission.key, result)
                final.append(_result_event(result))
            except (GitHubError, httpx.HTTPError) as error:
                status, detail= _failure(error)
                final.append({"type": "error", "status": status, "detail": detail})
            except Exception:
                final.append({"type": "error", "status": 500, "detail": "A análise falhou"})
            wake.set()

        task= asyncio.create_task(work())
        try:
            while True:
                while pending:
                    yield _line(pending.popleft())
                if final:
                    yield _line(final[0])
                    break
                wake.clear()
                if pending or final:
                    continue
                await wake.wait()
        finally:
            # o cliente desligou (ou acabou): pára-se o trabalho e devolve-se a vaga
            task.cancel()
            admission.lease.release()

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
