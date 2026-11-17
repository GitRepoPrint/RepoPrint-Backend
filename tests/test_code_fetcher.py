"""Descarregar o código de um repositório: listar os ficheiros pela Git Trees API e pedir só os de código ao raw.githubusercontent.com,
em paralelo. Para repositórios enormes (o Linux) escolhe-se uma amostra justa em vez de tentar ler tudo."""
import json
from urllib.parse import quote, unquote

import httpx
import pytest

from app.config import settings
from app.services import code_fetcher as fetcher
from app.services.code_fetcher import SourceFile
from app.services.github import GitHubError

API= "https://api.github.com"
RAW= "https://raw.githubusercontent.com"


def sha_of(path: str) -> str:
    """Um sha de brincar que se desfaz no caminho (os verdadeiros são só letras e números)."""
    return "sha:" + quote(path, safe="")


def blob(path: str, size: int= 100) -> dict:
    return {"path": path, "type": "blob", "size": size, "sha": sha_of(path)}


def tree(path: str) -> dict:
    return {"path": path, "type": "tree", "sha": f"sha-{path}"}


class Fake:
    """Um GitHub de brincar: responde à listagem e aos ficheiros, e regista os pedidos."""

    def __init__(self, entries=None, files=None, *, tree_handler=None, raw_handler=None):
        self.entries= entries or []
        self.files= files or {}
        self.tree_handler= tree_handler
        self.raw_handler= raw_handler
        self.requests: list[str]= []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url= str(request.url)
        self.requests.append(url)
        if url.startswith(API) and "/git/trees/" in url:
            if self.tree_handler:
                return self.tree_handler(request)
            return httpx.Response(200, json={"sha": "root", "tree": self.entries, "truncated": False})
        if url.startswith(RAW):
            if self.raw_handler:
                return self.raw_handler(request)
            path= url.split("/HEAD/", 1)[1]
            return httpx.Response(200, content=self.files[path]) if path in self.files else httpx.Response(404)
        return httpx.Response(500)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), base_url=API, follow_redirects=True)


class TestChoosingWhatToRead:
    def test_only_source_code_of_a_reasonable_size_is_a_candidate(self):
        entries= [
            blob("app/main.py"), blob("README.md"), blob("docs/conf.py"), blob("node_modules/x/index.js"),
            blob("app/data.json"), blob("app/empty.py", 0), blob("app/huge.py", 5 * 1024 * 1024), blob("web/app.min.js"),
            tree("app"), blob("web/index.html"),
        ]
        paths= [c["path"] for c in fetcher.candidate_files(entries)]
        assert sorted(paths) == ["app/main.py", "web/index.html"]

    def test_compiled_and_binary_files_are_never_chosen_even_if_a_language_claims_them(self):
        # .mo (catálogos de traduções do gettext) parece-se com Modelica; o Django tem mais de mil
        entries= [blob("app/main.py"), blob("django/conf/locale/af/LC_MESSAGES/django.mo"), blob("lib/x.pyc"), blob("lib/Native.class"), blob("a/b.so")]
        assert [c["path"] for c in fetcher.candidate_files(entries)] == ["app/main.py"]

    def test_a_small_repository_is_read_whole_in_a_stable_order(self):
        candidates= fetcher.candidate_files([blob("b/two.py"), blob("a/one.py"), blob("a/zero.py")])
        chosen= fetcher.sample_files(candidates, max_files=10, max_bytes=10_000)
        assert [c["path"] for c in chosen] == ["a/one.py", "a/zero.py", "b/two.py"]

    def test_a_big_repository_is_sampled_within_the_limit(self):
        entries= [blob(f"drivers/d{d:03}/f{f}.c") for d in range(200) for f in range(10)]
        chosen= fetcher.sample_files(fetcher.candidate_files(entries), max_files=500, max_bytes=10**9)
        assert len(chosen) == 500
        assert len({c["path"] for c in chosen}) == 500

    def test_the_sample_spreads_over_every_top_level_folder_before_going_deep(self):
        entries= [blob(f"arch/x{d}/f{f}.c") for d in range(300) for f in range(5)]  # uma pasta enorme
        entries+= [blob(f"{top}/main.c") for top in ("block", "crypto", "fs", "kernel", "mm", "net")]
        chosen= {c["path"] for c in fetcher.sample_files(fetcher.candidate_files(entries), max_files=50, max_bytes=10**9)}
        for top in ("block", "crypto", "fs", "kernel", "mm", "net"):
            assert f"{top}/main.c" in chosen, f"{top} ficou de fora"

    def test_the_sample_does_not_let_one_folder_take_everything(self):
        entries= [blob(f"big/f{i}.py") for i in range(400)] + [blob(f"small{n}/f.py") for n in range(40)]
        chosen= fetcher.sample_files(fetcher.candidate_files(entries), max_files=60, max_bytes=10**9)
        assert sum(c["path"].startswith("big/") for c in chosen) <= 20

    def test_the_sample_is_the_same_every_time(self):
        entries= [blob(f"d{d}/f{f}.py") for d in range(50) for f in range(20)]
        first= fetcher.sample_files(fetcher.candidate_files(entries), max_files=100, max_bytes=10**9)
        assert first == fetcher.sample_files(fetcher.candidate_files(list(reversed(entries))), max_files=100, max_bytes=10**9)

    def test_when_it_does_not_all_fit_product_code_comes_before_tests_examples_and_scripts(self):
        product= [blob(f"src/m{d}/f{f}.py") for d in range(20) for f in range(10)]  # 200
        aux= [blob(f"tests/t{d}/f{f}.py") for d in range(20) for f in range(10)] + [blob(f"examples/e{i}.py") for i in range(50)]
        chosen= [c["path"] for c in fetcher.sample_files(fetcher.candidate_files(product + aux), max_files=150, max_bytes=10**9)]
        assert len(chosen) == 150
        assert all(p.startswith("src/") for p in chosen)

    def test_tests_and_examples_fill_what_the_product_code_leaves_over(self):
        product= [blob(f"src/m{i}.py") for i in range(30)]
        aux= [blob(f"tests/t{i}.py") for i in range(100)]
        chosen= [c["path"] for c in fetcher.sample_files(fetcher.candidate_files(product + aux), max_files=50, max_bytes=10**9)]
        assert len(chosen) == 50
        assert sum(p.startswith("src/") for p in chosen) == 30 and sum(p.startswith("tests/") for p in chosen) == 20

    def test_a_repository_that_is_only_tests_or_docs_still_gets_a_sample(self):
        chosen= fetcher.sample_files(fetcher.candidate_files([blob(f"tests/t{i}.py") for i in range(40)]), max_files=10, max_bytes=10**9)
        assert len(chosen) == 10

    @pytest.mark.parametrize("path, expected", [
        ("tests/test_a.py", True), ("src/tests/unit/a.py", True), ("docs_src/t/a.py", True), ("examples/a.py", True), ("scripts/a.py", True),
        ("benchmarks/a.py", True), ("pkg/test_models.py", False), ("tests_utils/a.py", False), ("src/app/a.py", False), ("latest/a.py", False),
        ("contests/a.py", False), ("pkg/foo_tests/a.py", True), ("e2e/a.py", True),
    ])
    def test_which_folders_count_as_tests_docs_and_examples(self, path, expected):
        assert fetcher.is_auxiliary(path) is expected

    def test_the_byte_budget_is_respected(self):
        entries= [blob(f"d{i}/f.py", 1000) for i in range(100)]
        chosen= fetcher.sample_files(fetcher.candidate_files(entries), max_files=1000, max_bytes=10_000)
        assert sum(c["size"] for c in chosen) <= 10_000
        assert len(chosen) == 10


@pytest.mark.anyio
class TestListingTheRepository:
    async def test_a_normal_repository_is_listed_with_one_request(self):
        fake= Fake([blob("a.py"), tree("pkg"), blob("pkg/b.py")])
        listing= await fetcher.list_tree(fake.client(), "o", "r")
        assert [e["path"] for e in listing.entries] == ["a.py", "pkg", "pkg/b.py"]
        assert listing.truncated is False
        assert len(fake.requests) == 1
        assert "/repos/o/r/git/trees/HEAD" in fake.requests[0] and "recursive=1" in fake.requests[0]

    async def test_a_repository_too_big_for_one_listing_is_listed_folder_by_folder(self):
        def handler(request):
            url= str(request.url)
            if "recursive" in url and "/trees/HEAD" in url:
                return httpx.Response(200, json={"sha": "root", "tree": [blob("a.py")], "truncated": True})
            if url.endswith("/trees/root"):
                return httpx.Response(200, json={"sha": "root", "tree": [blob("top.c"), tree("drivers"), tree("fs")], "truncated": False})
            if "sha-drivers" in url:
                return httpx.Response(200, json={"tree": [blob("net/e1000.c"), tree("net")], "truncated": False})
            if "sha-fs" in url:
                return httpx.Response(200, json={"tree": [blob("ext4.c")], "truncated": False})
            return httpx.Response(500)

        listing= await fetcher.list_tree(Fake(tree_handler=handler).client(), "torvalds", "linux")
        paths= sorted(e["path"] for e in listing.entries if e["type"] == "blob")
        assert paths == ["drivers/net/e1000.c", "fs/ext4.c", "top.c"]
        assert listing.truncated is False  # as partes juntas são o repositório todo

    async def test_a_folder_that_is_still_too_big_marks_the_listing_as_incomplete(self):
        def handler(request):
            url= str(request.url)
            if "/trees/HEAD" in url:
                return httpx.Response(200, json={"sha": "root", "tree": [], "truncated": True})
            if url.endswith("/trees/root"):
                return httpx.Response(200, json={"tree": [tree("drivers")], "truncated": False})
            return httpx.Response(200, json={"tree": [blob("a.c")], "truncated": True})

        listing= await fetcher.list_tree(Fake(tree_handler=handler).client(), "o", "r")
        assert listing.truncated is True
        assert [e["path"] for e in listing.entries] == ["drivers/a.c"]

    @pytest.mark.parametrize("status, headers, expected", [
        (404, {}, (404, "Repositório não encontrado ou privado")),
        (403, {"x-ratelimit-remaining": "0"}, (429, "Rate limit do GitHub excedido")),
        (409, {}, (422, "Repositório vazio")),
        (500, {}, (502, "GitHub respondeu 500")),
    ])
    async def test_errors_become_clean_messages(self, status, headers, expected):
        fake= Fake(tree_handler=lambda request: httpx.Response(status, headers=headers))
        with pytest.raises(GitHubError) as error:
            await fetcher.list_tree(fake.client(), "o", "r")
        assert (error.value.status, error.value.message) == expected


@pytest.mark.anyio
class TestReadingTheFiles:
    async def test_the_files_are_read_in_parallel_and_returned_in_order(self):
        chosen= [{"path": f"d/f{i}.py", "size": 5, "language": "python"} for i in range(30)]
        fake= Fake(files={c["path"]: b"x = 1\n" for c in chosen})
        files, failed= await fetcher.fetch_sources(fake.client(), "o", "r", chosen, concurrency=8)
        assert [f.path for f in files] == [c["path"] for c in chosen]
        assert all(isinstance(f, SourceFile) and f.language == "python" and f.source == b"x = 1\n" for f in files)
        assert failed == 0

    async def test_binary_files_and_missing_files_are_skipped(self):
        chosen= [{"path": p, "size": 5, "language": "python"} for p in ("ok.py", "bin.py", "gone.py")]
        fake= Fake(files={"ok.py": b"a = 1\n", "bin.py": b"\0\1\2binary"})
        files, failed= await fetcher.fetch_sources(fake.client(), "o", "r", chosen)
        assert [f.path for f in files] == ["ok.py"]
        assert failed == 1  # o que não existe conta como falha; o binário é só ignorado

    async def test_a_server_error_is_retried_once(self):
        attempts= {"n": 0}

        def raw(request):
            attempts["n"] += 1
            return httpx.Response(502) if attempts["n"] == 1 else httpx.Response(200, content=b"a = 1\n")

        files, failed= await fetcher.fetch_sources(Fake(raw_handler=raw).client(), "o", "r", [{"path": "a.py", "size": 5, "language": "python"}])
        assert [f.path for f in files] == ["a.py"] and failed == 0 and attempts["n"] == 2

    async def test_a_rate_limit_stops_everything(self):
        fake= Fake(raw_handler=lambda request: httpx.Response(429))
        with pytest.raises(GitHubError) as error:
            await fetcher.fetch_sources(fake.client(), "o", "r", [{"path": "a.py", "size": 5, "language": "python"}])
        assert error.value.status == 429

    async def test_progress_counts_up_to_the_total(self):
        chosen= [{"path": f"f{i}.py", "size": 5, "language": "python"} for i in range(60)]
        events= []
        fake= Fake(files={c["path"]: b"a = 1\n" for c in chosen})
        await fetcher.fetch_sources(fake.client(), "o", "r", chosen, concurrency=8, progress=lambda stage, done, total: events.append((stage, done, total)))
        assert events and all(stage == "downloading" and total == 60 for stage, _, total in events)
        dones= [d for _, d, _ in events]
        assert dones == sorted(dones) and dones[-1] == 60
        assert len(events) < 60  # não é um evento por ficheiro


@pytest.mark.anyio
class TestWholeRepository:
    @pytest.fixture(autouse=True)
    def per_file_fetching(self, monkeypatch):
        monkeypatch.setattr(settings, "dfd_fetch_method", "auto")
        monkeypatch.setattr(settings, "github_token", None)  # sem token, os ficheiros vêm um a um do raw

    async def test_the_source_files_are_listed_then_read(self):
        fake= Fake([blob("app/main.py"), blob("README.md"), blob("app/db.py")], {"app/main.py": b"import os\n", "app/db.py": b"import sqlite3\n"})
        result= await fetcher.fetch_repository_files("o", "r", client=fake.client())
        assert sorted(f.path for f in result.files) == ["app/db.py", "app/main.py"]
        assert (result.method, result.candidates, result.sampled, result.truncated) == ("tree", 2, False, False)
        assert not any("tarball" in url for url in fake.requests)

    async def test_a_huge_repository_is_sampled_and_says_so(self, monkeypatch):
        monkeypatch.setattr(fetcher, "MAX_SAMPLED_FILES", 40)
        entries= [blob(f"d{d}/f{f}.c") for d in range(30) for f in range(10)]
        fake= Fake(entries, {e["path"]: b"int x;\n" for e in entries})
        result= await fetcher.fetch_repository_files("o", "r", client=fake.client())
        assert len(result.files) == 40
        assert (result.candidates, result.sampled) == (300, True)

    async def test_progress_goes_through_listing_downloading_and_ends_complete(self):
        fake= Fake([blob("a.py"), blob("b.py")], {"a.py": b"a = 1\n", "b.py": b"b = 1\n"})
        events= []
        await fetcher.fetch_repository_files("o", "r", client=fake.client(), progress=lambda *e: events.append(e))
        stages= [e[0] for e in events]
        assert stages[0] == "listing" and stages[-1] == "downloading"
        assert events[-1][1] == events[-1][2] == 2

    async def test_when_most_files_cannot_be_read_it_falls_back_to_the_archive(self):
        fake= Fake([blob(f"f{i}.py") for i in range(10)], raw_handler=lambda request: httpx.Response(500))
        loaded= []

        async def archive_loader():
            import io, tarfile
            buffer= io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                data= b"import os\n"
                info= tarfile.TarInfo("o-r-abc/main.py")
                info.size= len(data)
                tar.addfile(info, io.BytesIO(data))
            buffer.seek(0)
            loaded.append(True)
            return buffer

        result= await fetcher.fetch_repository_files("o", "r", client=fake.client(), archive_loader=archive_loader)
        assert loaded and result.method == "archive"
        assert [f.path for f in result.files] == ["main.py"]

    async def test_a_missing_repository_is_not_retried_with_the_archive(self):
        fake= Fake(tree_handler=lambda request: httpx.Response(404))

        async def archive_loader():
            raise AssertionError("não devia chegar aqui")

        with pytest.raises(GitHubError) as error:
            await fetcher.fetch_repository_files("o", "r", client=fake.client(), archive_loader=archive_loader)
        assert error.value.status == 404

    async def test_the_archive_method_never_lists_the_tree(self, monkeypatch):
        monkeypatch.setattr(settings, "dfd_fetch_method", "archive")
        fake= Fake([blob("a.py")], {"a.py": b"a = 1\n"})

        async def archive_loader():
            import io
            return io.BytesIO(b"")  # arquivo vazio: não é um tar válido

        with pytest.raises(Exception):
            await fetcher.fetch_repository_files("o", "r", client=fake.client(), archive_loader=archive_loader)
        assert fake.requests == []


def graphql_handler(files: dict[str, str | None], *, binary: set[str]= frozenset(), calls: list | None= None, status: int= 200, errors=None):
    """Um GraphQL de brincar: lê os `oid` do pedido (o sha de cada ficheiro, como a listagem os dá) e responde com o texto de cada um."""
    import re

    def handler(request: httpx.Request) -> httpx.Response:
        body= json.loads(request.content)
        if calls is not None:
            calls.append(body)
        if status != 200:
            return httpx.Response(status)
        if errors:
            return httpx.Response(200, json={"data": None, "errors": errors})
        repository= {}
        for alias, oid in re.findall(r'(\w+):object\(oid:\s*("[^"]*")\)', body["query"]):
            path= unquote(json.loads(oid).removeprefix("sha:"))
            if path in binary:
                repository[alias]= {"text": None, "isBinary": True}
            elif files.get(path) is not None:
                repository[alias]= {"text": files[path], "isBinary": False}
            else:
                repository[alias]= None
        return httpx.Response(200, json={"data": {"repository": repository}})

    return handler


class _GraphqlFake(Fake):
    def __init__(self, files, **kwargs):
        super().__init__()
        self.handler= graphql_handler(files, **kwargs)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return self.handler(request)


def item(path: str, size: int= 5) -> dict:
    return {"path": path, "size": size, "language": "python", "sha": sha_of(path)}


def items(count: int, size: int= 100) -> list[dict]:
    return [item(f"pkg/m{i:03}.py", size) for i in range(count)]


@pytest.mark.anyio
class TestReadingManyFilesInOneRequest:
    async def test_many_files_are_read_in_a_few_requests_and_come_back_in_order(self):
        chosen= items(230)
        calls= []
        fake= _GraphqlFake({c["path"]: f"# {c['path']}\n" for c in chosen}, calls=calls)
        files, failed= await fetcher.fetch_sources_graphql(fake.client(), "o", "r", chosen)
        assert [f.path for f in files] == [c["path"] for c in chosen]
        assert files[7].source == b"# pkg/m007.py\n" and files[7].language == "python"
        assert failed == 0
        assert 3 <= len(calls) <= 6  # e não 230 pedidos

    async def test_each_request_is_limited_in_files_and_in_bytes(self):
        chosen= items(300, size=1000) + [item(f"big/b{i}.py", 900_000) for i in range(10)]
        calls= []
        fake= _GraphqlFake({c["path"]: "x = 1\n" for c in chosen}, calls=calls)
        await fetcher.fetch_sources_graphql(fake.client(), "o", "r", chosen)
        for body in calls:
            count= body["query"].count(":object(")
            assert count <= fetcher.GRAPHQL_BATCH_FILES
            assert sum(c["size"] for c in chosen if f'"{c["sha"]}"' in body["query"]) <= fetcher.GRAPHQL_BATCH_BYTES

    async def test_the_repository_goes_in_variables_and_not_in_the_query_text(self):
        calls= []
        fake= _GraphqlFake({"a.py": "a = 1\n"}, calls=calls)
        await fetcher.fetch_sources_graphql(fake.client(), "an\"owner", "re$po", [item("a.py")])
        assert calls[0]["variables"] == {"owner": "an\"owner", "name": "re$po"}
        assert "an\"owner" not in calls[0]["query"]

    async def test_files_are_asked_for_by_their_sha_which_is_much_faster_than_by_path(self):
        calls= []
        fake= _GraphqlFake({'dir/a "quoted" \\ file.py': "a = 1\n"}, calls=calls)
        weird= 'dir/a "quoted" \\ file.py'
        files, _= await fetcher.fetch_sources_graphql(fake.client(), "o", "r", [item(weird)])
        assert [f.path for f in files] == [weird]
        query= calls[0]["query"]
        assert "oid:" in query and "expression" not in query and "HEAD:" not in query

    async def test_missing_and_binary_files(self):
        chosen= [item(p) for p in ("ok.py", "bin.py", "gone.py")]
        fake= _GraphqlFake({"ok.py": "a = 1\n", "gone.py": None}, binary={"bin.py"})
        files, failed= await fetcher.fetch_sources_graphql(fake.client(), "o", "r", chosen)
        assert [f.path for f in files] == ["ok.py"]
        assert failed == 1  # o que não existe conta; o binário só se ignora

    async def test_a_rate_limit_stops_everything(self):
        fake= _GraphqlFake({}, errors=[{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}])
        with pytest.raises(GitHubError) as error:
            await fetcher.fetch_sources_graphql(fake.client(), "o", "r", items(3))
        assert error.value.status == 429

    async def test_a_server_error_is_retried_once_and_then_counts_as_failed_files(self):
        calls= []
        fake= _GraphqlFake({}, calls=calls, status=502)
        files, failed= await fetcher.fetch_sources_graphql(fake.client(), "o", "r", items(5))
        assert files == [] and failed == 5
        assert len(calls) == 2

    async def test_progress_counts_up_to_the_total(self):
        events= []
        chosen= items(230)
        fake= _GraphqlFake({c["path"]: "a = 1\n" for c in chosen})
        await fetcher.fetch_sources_graphql(fake.client(), "o", "r", chosen, progress=lambda *e: events.append(e))
        assert events and all(stage == "downloading" and total == 230 for stage, _, total in events)
        dones= [d for _, d, _ in events]
        assert dones == sorted(dones) and dones[-1] == 230


@pytest.mark.anyio
class TestChoosingTheMethod:
    @pytest.fixture(autouse=True)
    def per_file_fetching(self, monkeypatch):
        monkeypatch.setattr(settings, "dfd_fetch_method", "auto")

    class Both(Fake):
        """Listagem, raw e GraphQL no mesmo sítio, para ver qual se usa."""

        def __init__(self, entries, files):
            super().__init__(entries, files)
            self.graphql= graphql_handler(files)

        def __call__(self, request):
            if str(request.url).endswith("/graphql"):
                self.requests.append("graphql")
                return self.graphql(request)
            return super().__call__(request)

    async def test_with_a_token_the_files_come_through_graphql(self, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_test")
        both= self.Both([blob("a.py"), blob("b.py")], {"a.py": "a = 1\n", "b.py": "b = 1\n"})
        result= await fetcher.fetch_repository_files("o", "r", client=both.client())
        assert sorted(f.path for f in result.files) == ["a.py", "b.py"] and result.method == "graphql"
        assert not any(url.startswith(RAW) for url in both.requests)

    async def test_without_a_token_they_come_from_raw(self, monkeypatch):
        monkeypatch.setattr(settings, "github_token", None)
        both= self.Both([blob("a.py")], {"a.py": "a = 1\n"})
        result= await fetcher.fetch_repository_files("o", "r", client=both.client())
        assert result.method == "tree" and "graphql" not in both.requests

    async def test_if_graphql_cannot_read_the_files_it_falls_back_to_the_archive(self, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_test")
        both= self.Both([blob(f"f{i}.py") for i in range(6)], {})  # o GraphQL não encontra nada
        loaded= []

        async def archive_loader():
            import io
            loaded.append(1)
            import tarfile
            buffer= io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                info= tarfile.TarInfo("o-r-abc/m.py")
                info.size= 3
                tar.addfile(info, io.BytesIO(b"a=1"))
            buffer.seek(0)
            return buffer

        result= await fetcher.fetch_repository_files("o", "r", client=both.client(), archive_loader=archive_loader)
        assert loaded and result.method == "archive"

    async def test_a_big_sample_is_only_taken_when_the_method_can_afford_it(self, monkeypatch):
        entries= [blob(f"d{d}/f{f}.c") for d in range(60) for f in range(30)]  # 1800 ficheiros
        files= {e["path"]: "int x;\n" for e in entries}
        monkeypatch.setattr(settings, "github_token", None)
        raw= await fetcher.fetch_repository_files("o", "r", client=self.Both(entries, {k: v.encode() for k, v in files.items()}).client())
        monkeypatch.setattr(settings, "github_token", "ghp_test")
        graph= await fetcher.fetch_repository_files("o", "r", client=self.Both(entries, files).client())
        assert len(raw.files) == fetcher.MAX_SAMPLED_FILES_RAW and raw.sampled
        assert len(graph.files) == 1800 and not graph.sampled
