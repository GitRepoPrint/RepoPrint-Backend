import asyncio
import re
import tarfile

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import User
from app.routers.dfd import resolve_private_token
from app.services.auth import get_current_user
from app.services.code_fetcher import download_archive
from app.services.github import GitHubError, parse_repo_url

router= APIRouter(prefix="/dfd/source", tags=["dfd"])

MAX_FILE_BYTES= 200_000
MAX_FILES= 500
MAX_TOTAL_BYTES= 20 * 1024 * 1024

SKIP_DIRS= {
    "node_modules", "vendor", "dist", "target", "out", ".git", ".venv", "venv", "env", "__pycache__", ".next", ".nuxt",
    "coverage", "third_party", "bower_components", "site-packages", ".idea", ".vscode", ".github",
    "docs", "doc", "documentation", "examples", "example", "samples", "sample",
    "test", "tests", "__tests__", "spec", "specs", "e2e", "fixtures", "testdata",
}
MANIFEST_NAMES= {
    "package.json", "pyproject.toml", "requirements.txt", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml",
}
COMPOSE= re.compile(r"^(docker-)?compose([.-][\w.-]+)?\.ya?ml$", re.IGNORECASE)
DOCKERFILE= re.compile(r"^dockerfile(\..+)?$|\.dockerfile$", re.IGNORECASE)
CONFIG= re.compile(r"^(application\.(ya?ml|properties)|\.env\.(example|sample|template)|example\.env)$", re.IGNORECASE)


def is_candidate(path: str) -> bool:
    parts= path.split("/")
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return False
    name= parts[-1]
    return bool(COMPOSE.match(name) or DOCKERFILE.search(name) or name in MANIFEST_NAMES or CONFIG.match(name))


def read_config(archive) -> dict:
    tree: list[dict]= []
    files: dict[str, str]= {}
    total= 0
    truncated= False
    with tarfile.open(fileobj=archive, mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.size == 0 or member.size > MAX_FILE_BYTES:
                continue
            _, _, path= member.name.partition("/")
            if not path or not is_candidate(path):
                continue
            if len(files) >= MAX_FILES or total + member.size > MAX_TOTAL_BYTES:
                truncated= True
                continue
            data= tar.extractfile(member).read()
            if b"\0" in data[:8000]:
                continue
            tree.append({"path": path, "type": "blob", "size": len(data)})
            files[path]= data.decode("utf-8", errors="replace")
            total+= len(data)
    return {"tree": tree, "truncated": truncated, "files": files}


@router.get("")
async def source(
    request: Request,
    url: str= Query(...),
    user: User= Depends(get_current_user),
    session: AsyncSession= Depends(get_session),
):
    try:
        owner, repo= parse_repo_url(url)
    except GitHubError as error:
        raise HTTPException(error.status, error.message)

    guard= request.app.state.guard
    key= ("source", str(user.id), owner.lower(), repo.lower())
    cached= guard.cache.get(key)
    if cached is not None:
        return cached

    wait= guard.limiter.check(guard.client_id(request.client.host if request.client else None, request.headers.get("x-forwarded-for")))
    if wait > 0:
        raise HTTPException(429, "Demasiados pedidos: tente mais tarde", headers={"Retry-After": str(int(wait) + 1)})
    lease= await guard.lease()
    if lease is None:
        raise HTTPException(503, "Servidor ocupado: tente mais tarde", headers={"Retry-After": "10"})
    try:
        token= await resolve_private_token(user, session, owner, repo)
        archive= await download_archive(owner, repo, token)
        try:
            result= await asyncio.to_thread(read_config, archive)
        finally:
            archive.close()
    except GitHubError as error:
        raise HTTPException(error.status, error.message)
    except httpx.HTTPError:
        raise HTTPException(502, "Falha ao contactar o GitHub")
    finally:
        lease.release()
    guard.cache.set(key, result)
    return result
