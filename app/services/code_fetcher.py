import io
import tarfile
import tempfile
from dataclasses import dataclass

import httpx
from tree_sitter_language_pack import detect_language_from_path

from app.services.github import API, GitHubError, _headers

MAX_ARCHIVE_BYTES= 100 * 1024 * 1024
MAX_FILE_BYTES= 1024 * 1024
MAX_TOTAL_BYTES= 60 * 1024 * 1024
MAX_FILES= 3000

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
SKIP_SUFFIXES= (".min.js", ".min.css", ".bundle.js", ".map", ".lock")


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
        async with httpx.AsyncClient(base_url=API, headers=_headers(), timeout=60, follow_redirects=True) as client:
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
