"""GET /dfd/stream: a mesma análise do GET /dfd, mas em NDJSON: uma linha por evento de avanço e, no fim, uma com o resultado.
O interface usa-a para mostrar o progresso a sério em vez de uma roda a girar."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from app.config import Settings
from app.routers import dfd as dfd_router
from app.services.code_fetcher import FetchResult, SourceFile
from app.services.github import GitHubError
from app.services.guard import DfdGuard, Lease
from tests.test_dfd_schema import FILES, archive_of

URL= "https://github.com/owner/repo"


def app_with(monkeypatch, **settings):
    monkeypatch.setattr(main, "settings", Settings(_env_file=None, **{"dfd_rate_limit": 0, **settings}))
    return main.create_app()


@pytest.fixture
def downloads(monkeypatch):
    calls= []

    async def fake_download(owner, repo):
        calls.append((owner, repo))
        return archive_of(FILES)

    monkeypatch.setattr(dfd_router, "download_archive", fake_download)
    return calls


def events_of(client: TestClient, repo_url: str= URL, **params) -> list[dict]:
    with client.stream("GET", "/dfd/stream", params={"url": repo_url, **params}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in response.iter_lines() if line.strip()]


class TestTheStream:
    def test_it_sends_progress_and_ends_with_the_same_result_as_the_plain_endpoint(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch))
        events= events_of(client)
        assert events[-1]["type"] == "result"
        assert all(e["type"] == "progress" for e in events[:-1])
        assert events[-1]["graph"] == client.get("/dfd", params={"url": URL}).json() or events[-1]["graph"]["nodes"]
        plain= client.get("/dfd", params={"url": URL}).json()
        assert [n["id"] for n in events[-1]["graph"]["nodes"]] == [n["id"] for n in plain["nodes"]]

    def test_the_phases_come_in_order(self, monkeypatch, downloads):
        events= events_of(TestClient(app_with(monkeypatch)))
        stages= [e["stage"] for e in events if e["type"] == "progress"]
        order= {"listing": 0, "downloading": 1, "parsing": 2, "building": 3}
        assert stages and [order[s] for s in stages] == sorted(order[s] for s in stages)
        assert "parsing" in stages and stages[-1] == "building"

    def test_parsing_reports_how_many_files_are_done_out_of_how_many(self, monkeypatch, downloads):
        events= events_of(TestClient(app_with(monkeypatch)))
        parsing= [e for e in events if e.get("stage") == "parsing"]
        assert parsing[-1]["done"] == parsing[-1]["total"] == 4
        assert [e["done"] for e in parsing] == sorted(e["done"] for e in parsing)

    def test_a_repository_analysed_a_moment_ago_comes_straight_from_the_cache(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch))
        events_of(client)
        again= events_of(client)
        assert [e["type"] for e in again] == ["result"]
        assert len(downloads) == 1

    def test_the_plain_endpoint_and_the_stream_share_one_cache(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch))
        client.get("/dfd", params={"url": URL})
        assert [e["type"] for e in events_of(client)] == ["result"]
        assert len(downloads) == 1

    def test_the_result_says_how_the_code_was_read(self, monkeypatch, downloads):
        stats= events_of(TestClient(app_with(monkeypatch)))[-1]["graph"]["meta"]["stats"]
        assert (stats["files_parsed"], stats["files_found"], stats["sampled"], stats["fetch_method"]) == (4, 4, False, "archive")

    def test_a_failure_while_reading_the_repository_is_an_error_event(self, monkeypatch):
        async def missing(owner, repo):
            raise GitHubError(404, "Repositório não encontrado ou privado")

        monkeypatch.setattr(dfd_router, "download_archive", missing)
        events= events_of(TestClient(app_with(monkeypatch)))
        assert events[-1] == {"type": "error", "status": 404, "detail": "Repositório não encontrado ou privado"}

    def test_a_network_failure_is_a_clean_error_event(self, monkeypatch):
        async def broken(owner, repo):
            raise httpx.ConnectError("sem ligação")

        monkeypatch.setattr(dfd_router, "download_archive", broken)
        assert events_of(TestClient(app_with(monkeypatch)))[-1] == {"type": "error", "status": 502, "detail": "Falha ao contactar o GitHub"}

    def test_a_bad_address_is_refused_before_anything_streams(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch))
        response= client.get("/dfd/stream", params={"url": "isto nao e um url"})
        assert response.status_code == 400
        assert downloads == []

    def test_the_per_client_limit_still_applies_and_cached_answers_do_not_count(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_rate_limit=1))
        events_of(client, "https://github.com/o/a")
        assert events_of(client, "https://github.com/o/a")[-1]["type"] == "result"  # na cache: não conta
        refused= client.get("/dfd/stream", params={"url": "https://github.com/o/b"})
        assert refused.status_code == 429 and "retry-after" in refused.headers

    def test_cors_lets_the_page_read_the_stream(self, monkeypatch, downloads):
        monkeypatch.setattr(main, "settings", Settings(_env_file=None, cors_origins="https://app.test", dfd_rate_limit=0))
        client= TestClient(main.create_app())
        response= client.get("/dfd/stream", params={"url": URL}, headers={"Origin": "https://app.test"})
        assert response.headers["access-control-allow-origin"] == "https://app.test"


@pytest.mark.anyio
async def test_the_slot_is_given_back_when_the_stream_ends(monkeypatch, downloads):
    app= app_with(monkeypatch, dfd_max_concurrent=1, dfd_queue_timeout_seconds=0.05)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for repo in ("o/a", "o/b", "o/c"):  # com uma só vaga, só passam se cada uma a devolver
            response= await client.get("/dfd/stream", params={"url": f"https://github.com/{repo}"})
            assert response.status_code == 200 and json.loads(response.text.strip().splitlines()[-1])["type"] == "result"


@pytest.mark.anyio
async def test_a_busy_server_turns_a_streamed_analysis_away_with_503(monkeypatch):
    started= asyncio.Event()
    release= asyncio.Event()

    async def slow(owner, repo):
        started.set()
        await release.wait()
        return archive_of(FILES)

    monkeypatch.setattr(dfd_router, "download_archive", slow)
    app= app_with(monkeypatch, dfd_max_concurrent=1, dfd_queue_timeout_seconds=0.05)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first= asyncio.create_task(client.get("/dfd/stream", params={"url": "https://github.com/o/a"}))
        await started.wait()
        busy= await client.get("/dfd/stream", params={"url": "https://github.com/o/b"})
        assert busy.status_code == 503 and busy.headers["retry-after"] == "10"
        release.set()
        assert (await first).status_code == 200


@pytest.mark.anyio
class TestTheLease:
    async def test_releasing_twice_gives_back_only_one_slot(self):
        slots= asyncio.Semaphore(1)
        await slots.acquire()
        lease= Lease(slots, max_seconds=60)
        lease.release()
        lease.release()
        assert slots._value == 1

    async def test_a_lease_nobody_releases_is_given_back_after_its_time_limit(self):
        slots= asyncio.Semaphore(1)
        await slots.acquire()
        Lease(slots, max_seconds=0.05)
        await asyncio.sleep(0.15)
        assert slots._value == 1

    async def test_the_guard_hands_out_a_lease_or_none_when_busy(self):
        guard= DfdGuard.from_settings(Settings(_env_file=None, dfd_max_concurrent=1, dfd_queue_timeout_seconds=0.02))
        first= await guard.lease()
        assert first is not None
        assert await guard.lease() is None
        first.release()
        assert await guard.lease() is not None


class TestProgressOfTheParser:
    def test_it_reports_every_so_often_and_at_the_end(self):
        from app.services.dfd import parse_files

        files= [SourceFile(f"m{i}.py", "python", b"import os\n") for i in range(100)]
        seen= []
        parsed, _= parse_files(files, lambda done, total: seen.append((done, total)))
        assert len(parsed) == 100
        assert seen[-1] == (100, 100) and len(seen) < 100
        assert [d for d, _ in seen] == sorted(d for d, _ in seen)

    def test_it_still_works_without_a_callback(self):
        from app.services.dfd import parse_files

        assert len(parse_files([SourceFile("a.py", "python", b"import os\n")])[0]) == 1

    def test_the_order_of_the_files_is_kept(self):
        from app.services.dfd import parse_files

        files= [SourceFile(f"m{i:03}.py", "python", b"import os\n") for i in range(60)]
        assert [p.path for p in parse_files(files)[0]] == [f.path for f in files]


class TestParsingInParallel:
    """Num repositório grande o analisador usa vários processos (as threads não ajudam: o Python só corre uma de cada vez)."""

    @pytest.fixture(autouse=True)
    def low_threshold(self, monkeypatch):
        from app.services import dfd

        monkeypatch.setattr(dfd, "PARALLEL_MIN_FILES", 10)
        monkeypatch.setattr(dfd, "PARALLEL_WORKERS", 2)

    def files(self, n: int= 60) -> list[SourceFile]:
        code= b"import os\nimport requests\nrequests.get('https://api.example-site.io/v1')\ndef f():\n    return 1\n"
        return [SourceFile(f"pkg{i % 7}/m{i:03}.py", "python", code + b"# %d\n" % i) for i in range(n)]

    def test_it_gives_the_same_result_as_one_process_in_the_same_order(self, monkeypatch):
        from app.services import dfd

        files= self.files()
        parallel, _= dfd.parse_files(files)
        monkeypatch.setattr(dfd, "PARALLEL_MIN_FILES", 10**9)
        sequential, _= dfd.parse_files(files)
        assert [p.path for p in parallel] == [p.path for p in sequential] == [f.path for f in files]
        assert [(p.language, p.imports, p.definitions) for p in parallel] == [(p.language, p.imports, p.definitions) for p in sequential]

    def test_progress_still_counts_up_to_the_total(self):
        from app.services.dfd import parse_files

        seen= []
        parse_files(self.files(), lambda done, total: seen.append((done, total)))
        assert seen[-1] == (60, 60)
        assert [d for d, _ in seen] == sorted(d for d, _ in seen)

    def test_a_language_without_a_parser_is_reported_once_and_does_not_stop_the_rest(self):
        from app.services.dfd import parse_files

        files= self.files(30) + [SourceFile("x/a.nosuchlang", "nosuchlang", b"?"), SourceFile("x/b.nosuchlang", "nosuchlang", b"?")]
        parsed, failed= parse_files(files)
        assert len(parsed) == 30
        assert list(failed) == ["nosuchlang"]

    def test_a_small_repository_does_not_pay_for_extra_processes(self, monkeypatch):
        from app.services import dfd

        monkeypatch.setattr(dfd, "PARALLEL_MIN_FILES", 400)

        def boom(*args, **kwargs):
            raise AssertionError("não devia usar um grupo de processos")

        monkeypatch.setattr(dfd, "ProcessPoolExecutor", boom)
        assert len(dfd.parse_files(self.files(20))[0]) == 20


def test_a_result_from_the_new_fetcher_is_analysed_like_any_other(monkeypatch):
    async def fake_fetch(owner, repo, *, progress=None, archive_loader=None, client=None):
        progress and progress("listing", 0, 0)
        return FetchResult([SourceFile(p, "python", t.encode()) for p, t in FILES.items() if p.endswith(".py")], 900, True, True, "tree")

    monkeypatch.setattr(dfd_router, "fetch_repository_files", fake_fetch)
    stats= events_of(TestClient(app_with(monkeypatch)))[-1]["graph"]["meta"]["stats"]
    assert (stats["files_found"], stats["sampled"], stats["listing_truncated"], stats["fetch_method"]) == (900, True, True, "tree")
