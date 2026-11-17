import asyncio
import io
import json
import posixpath
import re
import tarfile
import tempfile
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import quote

import httpx
from tree_sitter_language_pack import detect_language_from_path

from app.config import settings
from app.services.github import GitHubError, _headers

MAX_ARCHIVE_BYTES= 100 * 1024 * 1024
MAX_FILE_BYTES= 1024 * 1024
MAX_TOTAL_BYTES= 60 * 1024 * 1024
MAX_FILES= 3000
# Descarregamento ficheiro a ficheiro (ver fetch_repository_files)
MAX_SAMPLED_FILES= 2500        # amostra máxima de um repositório muito grande (com token: pedidos GraphQL com 100 ficheiros cada)
MAX_SAMPLED_FILES_RAW= 1200    # sem token cada ficheiro é um pedido ao raw.githubusercontent.com, que também tem limite: amostra mais pequena
GRAPHQL_BATCH_FILES= 100       # ficheiros por pedido GraphQL
GRAPHQL_BATCH_BYTES= 4 * 1024 * 1024
GRAPHQL_CONCURRENCY= 6
TREE_CONCURRENCY= 8            # listagens de pastas ao mesmo tempo, quando o repositório é grande demais para uma só
MAX_FAILED_SHARE= 0.3          # se mais de 30% dos ficheiros não se leem, desiste-se deste método e usa-se o arquivo
PROGRESS_STEPS= 20             # quantas vezes, no máximo, se avisa do avanço de uma fase

Progress= Callable[[str, int, int], None]

# Linguagens que o tree-sitter-language-pack conhece mas que não descrevem fluxos de dados
NON_CODE= {
    "json", "json5", "jsonnet", "yaml", "toml", "ini", "xml", "csv", "tsv", "psv", "markdown", "markdown_inline",
    "rst", "asciidoc", "org", "latex", "bibtex", "css", "scss", "less", "diff", "gitignore",
    "gitattributes", "gitcommit", "git_config", "git_rebase", "editorconfig", "dotenv", "properties", "po", "rtf",
    "pem", "requirements", "pymanifest", "comment", "regex", "gomod", "gosum", "gowork", "todotxt", "vimdoc",
    "javadoc", "jsdoc", "luadoc", "phpdoc", "doxygen", "mermaid", "plantuml", "dot", "d2", "http", "hurl",
}
SKIP_DIRS= {
    ".git", "node_modules", "vendor", "dist", "build", "target", "out", "__pycache__", ".venv", "venv", "env",
    ".idea", ".vscode", "bower_components", "third_party", "site-packages", ".next", ".nuxt", "coverage",
    # documentação: configuração do Sphinx e afins só traz links, nunca fluxos do sistema
    "docs", "doc", "documentation",
}
SKIP_SUFFIXES= (
    ".min.js", ".min.css", ".bundle.js", ".map", ".lock",
    # compilados e binários: alguns têm a extensão de uma linguagem (".mo", os catálogos de traduções do gettext, parece Modelica: o
    # Django tem mais de mil) e gastavam a amostra para depois se descartarem
    ".mo", ".pyc", ".pyo", ".class", ".so", ".dll", ".dylib", ".exe", ".o", ".a", ".jar", ".whl",
)

# Pastas de testes, documentação, exemplos e scripts. Mantém-se igual a AUXILIARY_SEGMENT em RepoPrint-Frontend/src/lib/dfd/focus.ts:
# o diagrama esconde-as por defeito, por isso numa amostra vêm depois do código do produto.
AUXILIARY_SEGMENT= re.compile(
    r"^(tests?|__tests__|test_.*|.*_tests?|e2e|specs?|docs?|docs_src|documentation|examples?|samples?|demos?|scripts?|benchmarks?|fixtures?|mocks?|tools?|tutorials?)$",
    re.IGNORECASE,
)


@dataclass
class SourceFile:
    path: str
    language: str
    source: bytes


def _keep(path: str) -> str | None:
    parts= path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]) or path.endswith(SKIP_SUFFIXES):
        return None
    language= detect_language_from_path(path)
    if not language or language in NON_CODE:
        return None
    return language


def read_archive(archive: io.IOBase) -> list[SourceFile]:
    files: list[SourceFile]= []
    total= 0
    with tarfile.open(fileobj=archive, mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.size == 0 or member.size > MAX_FILE_BYTES:
                continue
            # o primeiro segmento é a pasta "owner-repo-sha" que o GitHub põe no tarball
            _, _, path= member.name.partition("/")
            language= _keep(path) if path else None
            if not language:
                continue
            data= tar.extractfile(member).read()
            if b"\0" in data[:8000]:
                continue
            total += len(data)
            files.append(SourceFile(path, language, data))
            if len(files) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
                break
    return files


async def download_archive(owner: str, repo: str) -> tempfile.SpooledTemporaryFile:
    spool= tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    try:
        async with httpx.AsyncClient(base_url=settings.github_api_url, headers=_headers(), timeout=60, follow_redirects=True) as client:
            async with client.stream("GET", f"/repos/{owner}/{repo}/tarball") as response:
                if response.is_error:
                    _raise_for(response)
                size= 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise GitHubError(413, "Repositório demasiado grande")
                    spool.write(chunk)
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise


def _raise_for(response: httpx.Response):
    if response.status_code == 404:
        raise GitHubError(404, "Repositório não encontrado ou privado")
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        raise GitHubError(429, "Rate limit do GitHub excedido")
    raise GitHubError(502, f"GitHub respondeu {response.status_code}")


# ---------------------------------------------------------------------------------------------------------------------
# Ficheiro a ficheiro: a Git Trees API lista tudo num pedido e o raw.githubusercontent.com dá cada ficheiro. Só se pedem os de
# código (o arquivo do GitHub é gerado na hora e traz tudo: 13 s para 18 MB no FastAPI, e nunca acaba no Linux).

@dataclass
class TreeListing:
    sha: str
    entries: list[dict]
    #: a listagem ficou incompleta (uma pasta é grande demais até para a API)
    truncated: bool


@dataclass
class FetchResult:
    files: list[SourceFile]
    #: ficheiros de código que o repositório tem
    candidates: int
    #: leram-se menos do que os que há (repositório muito grande)
    sampled: bool
    truncated: bool
    #: "graphql" (cerca de 100 ficheiros por pedido), "tree" (um pedido por ficheiro) ou "archive" (arquivo inteiro)
    method: str


class _Unreadable(Exception):
    """Mais ficheiros por ler do que o aceitável."""


def candidate_files(entries: list[dict]) -> list[dict]:
    """Dos ficheiros listados, os que valem a pena ler: código de tamanho razoável, fora das pastas que se ignoram."""
    found= []
    for entry in entries:
        size= entry.get("size") or 0
        if entry.get("type") != "blob" or size <= 0 or size > MAX_FILE_BYTES:
            continue
        language= _keep(entry["path"])
        if language:
            found.append({"path": entry["path"], "size": size, "language": language, "sha": entry.get("sha")})
    return found


def is_auxiliary(path: str) -> bool:
    """O ficheiro está numa pasta de testes, documentação, exemplos ou scripts (qualquer pasta do caminho; o nome do ficheiro não conta)."""
    return any(AUXILIARY_SEGMENT.match(segment) for segment in path.split("/")[:-1])


def _round_robin(candidates: list[dict], max_files: int, max_bytes: int) -> list[dict]:
    """Das pastas mais perto da raiz para as mais fundas, um ficheiro de cada pasta de cada vez."""
    groups: dict[str, list[dict]]= {}
    for candidate in candidates:
        groups.setdefault(posixpath.dirname(candidate["path"]), []).append(candidate)
    queues= [groups[name] for name in sorted(groups, key=lambda d: (d.count("/") if d else -1, d))]
    cursor= [0] * len(queues)

    chosen: list[dict]= []
    total= 0
    progressed= True
    while progressed and len(chosen) < max_files:
        progressed= False
        for index, queue in enumerate(queues):
            if cursor[index] >= len(queue):
                continue
            candidate= queue[cursor[index]]
            cursor[index] += 1
            progressed= True
            if total + candidate["size"] > max_bytes:
                continue
            chosen.append(candidate)
            total += candidate["size"]
            if len(chosen) >= max_files:
                break
    return chosen


def sample_files(candidates: list[dict], max_files: int, max_bytes: int) -> list[dict]:
    """Os ficheiros a ler. Se cabem todos, são todos. Senão, uma amostra justa (_round_robin) para que nenhuma pasta enorme
    (drivers/ no Linux) tire o lugar às outras, e com o código do produto à frente: só o que sobrar vai para testes, exemplos e scripts."""
    ordered= sorted(candidates, key=lambda c: c["path"])
    if len(ordered) <= max_files and sum(c["size"] for c in ordered) <= max_bytes:
        return ordered

    product= [c for c in ordered if not is_auxiliary(c["path"])]
    auxiliary= [c for c in ordered if is_auxiliary(c["path"])]
    chosen= _round_robin(product, max_files, max_bytes)
    if len(chosen) < max_files and auxiliary:
        chosen += _round_robin(auxiliary, max_files - len(chosen), max_bytes - sum(c["size"] for c in chosen))
    return sorted(chosen, key=lambda c: c["path"])


def _raise_listing_error(response: httpx.Response):
    if response.status_code == 409:
        raise GitHubError(422, "Repositório vazio")
    _raise_for(response)


async def list_tree(client: httpx.AsyncClient, owner: str, repo: str) -> TreeListing:
    base= f"/repos/{owner}/{repo}/git/trees"
    response= await client.get(f"{base}/HEAD", params={"recursive": "1"})
    if response.is_error:
        _raise_listing_error(response)
    data= response.json()
    if not data.get("truncated"):
        return TreeListing(data["sha"], data["tree"], False)

    # Grande demais para uma listagem (o limite é cerca de 100 000 entradas): lista-se a raiz e cada pasta de topo à parte
    root= await client.get(f"{base}/{data['sha']}")
    if root.is_error:
        _raise_listing_error(root)
    top= root.json()["tree"]
    entries= [e for e in top if e["type"] == "blob"]
    gate= asyncio.Semaphore(TREE_CONCURRENCY)

    async def folder(item: dict):
        async with gate:
            reply= await client.get(f"{base}/{item['sha']}", params={"recursive": "1"})
        if reply.status_code in (403, 429) and reply.headers.get("x-ratelimit-remaining") == "0":
            _raise_for(reply)
        if reply.is_error:
            return item["path"], [], True
        body= reply.json()
        return item["path"], [e for e in body["tree"] if e["type"] == "blob"], bool(body.get("truncated"))

    truncated= False
    for prefix, found, partial in await asyncio.gather(*(folder(item) for item in top if item["type"] == "tree")):
        truncated= truncated or partial
        entries.extend({**e, "path": f"{prefix}/{e['path']}"} for e in found)
    return TreeListing(data["sha"], entries, truncated)


async def fetch_sources(
    client: httpx.AsyncClient, owner: str, repo: str, chosen: list[dict], *, concurrency: int | None= None, progress: Progress | None= None,
) -> tuple[list[SourceFile], int]:
    """Lê os ficheiros em paralelo (na ordem em que vieram). Devolve os que se leram e quantos falharam (não existem ou o GitHub deu erro)."""
    total= len(chosen)
    gate= asyncio.Semaphore(concurrency or settings.dfd_fetch_concurrency)
    step= max(1, total // PROGRESS_STEPS)
    state= {"done": 0, "last": 0, "limited": False}

    async def one(item: dict) -> bytes | None | int:
        """bytes se leu; None se falhou; 0 se não vale (binário)."""
        url= f"{settings.github_raw_url}/{owner}/{repo}/HEAD/{quote(item['path'], safe='/')}"
        async with gate:
            result: bytes | None= None
            for attempt in range(2):
                if state["limited"]:
                    break
                try:
                    reply= await client.get(url)
                except httpx.HTTPError:
                    continue
                if reply.status_code == 200:
                    result= reply.content
                    break
                if reply.status_code == 429:
                    state["limited"]= True
                    break
                if reply.status_code < 500:
                    break
        state["done"] += 1
        if progress and (state["done"] - state["last"] >= step or state["done"] == total):
            state["last"]= state["done"]
            progress("downloading", state["done"], total)
        if result is not None and b"\0" in result[:8000]:
            return 0
        return result

    results= await asyncio.gather(*(one(item) for item in chosen))
    if state["limited"]:
        raise GitHubError(429, "Rate limit do GitHub excedido")
    files= [SourceFile(item["path"], item["language"], data) for item, data in zip(chosen, results) if isinstance(data, bytes)]
    return files, sum(data is None for data in results)


def _graphql_batches(chosen: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]]= [[]]
    size= 0
    for item in chosen:
        if batches[-1] and (len(batches[-1]) >= GRAPHQL_BATCH_FILES or size + item["size"] > GRAPHQL_BATCH_BYTES):
            batches.append([])
            size= 0
        batches[-1].append(item)
        size += item["size"]
    return [b for b in batches if b]


async def fetch_sources_graphql(
    client: httpx.AsyncClient, owner: str, repo: str, chosen: list[dict], *, progress: Progress | None= None,
) -> tuple[list[SourceFile], int]:
    """Como fetch_sources, mas pela API GraphQL: cerca de 100 ficheiros por pedido em vez de um pedido por ficheiro (o FastAPI são
    11 pedidos em vez de 1046, e uma amostra de 2500 ficheiros do Linux são 25). Precisa de um token do GitHub."""
    total= len(chosen)
    gate= asyncio.Semaphore(GRAPHQL_CONCURRENCY)
    state= {"done": 0, "limited": False}
    contents: dict[str, bytes | int | None]= {}

    async def one(batch: list[dict]) -> None:
        # Por sha (que a listagem já dá), não por caminho ("HEAD:caminho"): o GitHub encontra o ficheiro direto, em 0,6 s por 100 ficheiros
        # em vez de 2 a 5 s
        fields= "".join(f"f{i}:object(oid:{json.dumps(item['sha'])}){{...on Blob{{text isBinary}}}}" for i, item in enumerate(batch))
        query= f"query($owner:String!,$name:String!){{repository(owner:$owner,name:$name){{{fields}}}}}"
        repository: dict | None= None
        for attempt in range(2):
            if state["limited"]:
                break
            try:
                async with gate:
                    reply= await client.post("/graphql", json={"query": query, "variables": {"owner": owner, "name": repo}})
            except httpx.HTTPError:
                continue
            if reply.status_code == 200:
                body= reply.json()
                if any(e.get("type") == "RATE_LIMITED" for e in body.get("errors") or []):
                    state["limited"]= True
                    break
                if (body.get("data") or {}).get("repository") is not None:
                    repository= body["data"]["repository"]
                    break
            elif reply.status_code in (403, 429) and reply.headers.get("x-ratelimit-remaining") == "0":
                state["limited"]= True
                break
        for i, item in enumerate(batch):
            blob_= (repository or {}).get(f"f{i}")
            if blob_ is None:
                contents[item["path"]]= None
            elif blob_.get("isBinary") or blob_.get("text") is None:
                contents[item["path"]]= 0
            else:
                contents[item["path"]]= blob_["text"].encode()
        state["done"] += len(batch)
        if progress:
            progress("downloading", state["done"], total)

    await asyncio.gather(*(one(batch) for batch in _graphql_batches(chosen)))
    if state["limited"]:
        raise GitHubError(429, "Rate limit do GitHub excedido")
    files= [SourceFile(item["path"], item["language"], contents[item["path"]]) for item in chosen if isinstance(contents.get(item["path"]), bytes)]
    return files, sum(contents.get(item["path"]) is None for item in chosen)


async def _from_archive(owner: str, repo: str, progress: Progress | None, archive_loader: Callable[[], Awaitable] | None) -> FetchResult:
    if progress:
        progress("downloading", 0, 0)
    archive= await (archive_loader() if archive_loader else download_archive(owner, repo))
    try:
        files= await asyncio.to_thread(read_archive, archive)
    finally:
        archive.close()
    return FetchResult(files, len(files), False, False, "archive")


async def fetch_repository_files(
    owner: str, repo: str, *, progress: Progress | None= None, client: httpx.AsyncClient | None= None,
    archive_loader: Callable[[], Awaitable] | None= None,
) -> FetchResult:
    """O código de um repositório público. Lista os ficheiros e lê só os de código, em paralelo; num repositório muito grande lê uma
    amostra justa (FetchResult.sampled). Se isso não for possível (a rede falha, ou a maioria dos ficheiros não se lê) descarrega o
    arquivo inteiro. Um repositório que não existe ou um limite de pedidos do GitHub são erros e não se tentam de outra maneira."""
    if settings.dfd_fetch_method == "archive":
        return await _from_archive(owner, repo, progress, archive_loader)

    own= client is None
    if client is None:
        concurrency= settings.dfd_fetch_concurrency
        client= httpx.AsyncClient(
            base_url=settings.github_api_url, headers=_headers(), timeout=30, follow_redirects=True,
            limits=httpx.Limits(max_connections=concurrency + 8, max_keepalive_connections=concurrency + 8),
        )
    try:
        if progress:
            progress("listing", 0, 0)
        listing= await list_tree(client, owner, repo)
        candidates= candidate_files(listing.entries)
        # Com token, a API GraphQL dá cerca de 100 ficheiros por pedido: dá para ler muitos. Sem token cada ficheiro é um pedido ao
        # raw.githubusercontent.com (que tem limites) e a amostra é mais pequena.
        graphql= bool(settings.github_token)
        chosen= sample_files(candidates, MAX_SAMPLED_FILES if graphql else min(MAX_SAMPLED_FILES, MAX_SAMPLED_FILES_RAW), MAX_TOTAL_BYTES)
        if graphql:
            files, failed= await fetch_sources_graphql(client, owner, repo, chosen, progress=progress)
        else:
            files, failed= await fetch_sources(client, owner, repo, chosen, progress=progress)
        if chosen and failed > len(chosen) * MAX_FAILED_SHARE:
            raise _Unreadable()
        return FetchResult(files, len(candidates), len(chosen) < len(candidates), listing.truncated, "graphql" if graphql else "tree")
    except (_Unreadable, httpx.HTTPError):
        return await _from_archive(owner, repo, progress, archive_loader)
    finally:
        if own:
            await client.aclose()
