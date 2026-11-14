import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from app.config import Settings
from app.routers import dfd as dfd_router
from app.services.github import GitHubError
from app.services.guard import DfdGuard, RateLimiter, TtlCache
from tests.test_dfd_schema import FILES, archive_of


class Clock:
    def __init__(self):
        self.now= 0.0

    def __call__(self) -> float:
        return self.now


class TestTtlCache:
    def test_keeps_a_value_until_its_time_is_up(self):
        clock= Clock()
        cache= TtlCache(10, 5, clock)
        cache.set("a", 1)
        clock.now= 9.9
        assert cache.get("a") == 1
        clock.now= 10
        assert cache.get("a") is None
        assert len(cache) == 0

    def test_drops_the_one_not_used_for_longest_when_full(self):
        cache= TtlCache(100, 2, Clock())
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")  # "a" voltou a ser usado: "b" é o mais antigo
        cache.set("c", 3)
        assert (cache.get("a"), cache.get("b"), cache.get("c")) == (1, None, 3)

    @pytest.mark.parametrize("ttl, size", [(0, 5), (10, 0)])
    def test_can_be_turned_off(self, ttl, size):
        cache= TtlCache(ttl, size, Clock())
        cache.set("a", 1)
        assert cache.get("a") is None

    def test_does_not_confuse_a_missing_key_with_a_stored_false_value(self):
        cache= TtlCache(10, 5, Clock())
        cache.set("a", 0)
        assert cache.get("a") == 0


class TestRateLimiter:
    def test_lets_through_the_limit_and_says_how_long_to_wait_after_it(self):
        clock= Clock()
        limiter= RateLimiter(2, 60, clock)
        assert limiter.check("x") == 0
        clock.now= 10
        assert limiter.check("x") == 0
        clock.now= 20
        assert limiter.check("x") == pytest.approx(40)  # o primeiro pedido sai da janela aos 60 s

    def test_the_window_slides(self):
        clock= Clock()
        limiter= RateLimiter(1, 60, clock)
        assert limiter.check("x") == 0
        assert limiter.check("x") > 0
        clock.now= 60
        assert limiter.check("x") == 0

    def test_a_blocked_request_does_not_extend_the_block(self):
        clock= Clock()
        limiter= RateLimiter(1, 60, clock)
        limiter.check("x")
        for tick in (5, 10, 30):
            clock.now= tick
            assert limiter.check("x") > 0
        clock.now= 60
        assert limiter.check("x") == 0

    def test_each_client_has_its_own_allowance(self):
        limiter= RateLimiter(1, 60, Clock())
        assert limiter.check("a") == 0
        assert limiter.check("b") == 0
        assert limiter.check("a") > 0

    @pytest.mark.parametrize("limit", [0, -1])
    def test_can_be_turned_off(self, limit):
        limiter= RateLimiter(limit, 60, Clock())
        assert all(limiter.check("x") == 0 for _ in range(100))

    def test_does_not_remember_more_clients_than_it_can_afford(self, monkeypatch):
        monkeypatch.setattr(RateLimiter, "MAX_CLIENTS", 5)
        limiter= RateLimiter(1, 60, Clock())
        for n in range(20):
            limiter.check(f"client-{n}")
        assert len(limiter._hits) <= 5


class TestClientId:
    def guard(self, trust: bool) -> DfdGuard:
        return DfdGuard.from_settings(Settings(trust_forwarded_for=trust, _env_file=None))

    def test_uses_the_connection_address_by_default(self):
        assert self.guard(False).client_id("1.2.3.4", "9.9.9.9") == "1.2.3.4"

    def test_uses_the_first_forwarded_address_only_when_told_to_trust_it(self):
        assert self.guard(True).client_id("10.0.0.1", "9.9.9.9, 10.0.0.1") == "9.9.9.9"
        assert self.guard(True).client_id("10.0.0.1", None) == "10.0.0.1"
        assert self.guard(True).client_id("10.0.0.1", " , ") == "10.0.0.1"

    def test_copes_with_no_address_at_all(self):
        assert self.guard(False).client_id(None, None) == "unknown"


def app_with(monkeypatch, **settings):
    monkeypatch.setattr(main, "settings", Settings(_env_file=None, **settings))
    return main.create_app()


@pytest.fixture
def downloads(monkeypatch):
    """Cada análise descarrega um arquivo: contar os descarregamentos é contar as análises."""
    calls= []

    async def fake_download(owner, repo):
        calls.append((owner, repo))
        return archive_of(FILES)

    monkeypatch.setattr(dfd_router, "download_archive", fake_download)
    return calls


def get(client, repo="owner/repo", **params):
    return client.get("/dfd", params={"url": f"https://github.com/{repo}", **params})


class TestCache:
    def test_the_same_repository_is_analysed_once(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch))
        first, second= get(client), get(client)
        assert (first.status_code, second.status_code) == (200, 200)
        assert second.json() == first.json()
        assert len(downloads) == 1

    def test_the_name_of_the_repository_is_not_case_sensitive(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch))
        get(client, "Owner/Repo")
        get(client, "owner/repo")
        assert len(downloads) == 1

    def test_a_different_grouping_is_a_different_analysis(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch))
        get(client, group_by="directory")
        get(client, group_by="file")
        assert len(downloads) == 2

    def test_a_failure_is_not_remembered_and_does_not_keep_the_slot(self, monkeypatch):
        outcomes= [GitHubError(404, "Repositório não encontrado ou privado"), None]

        async def flaky(owner, repo):
            outcome= outcomes.pop(0)
            if outcome:
                raise outcome
            return archive_of(FILES)

        monkeypatch.setattr(dfd_router, "download_archive", flaky)
        client= TestClient(app_with(monkeypatch, dfd_max_concurrent=1))
        assert get(client).status_code == 404
        assert get(client).status_code == 200  # a vaga voltou e o erro não ficou na cache

    def test_it_can_be_turned_off(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_cache_ttl_seconds=0))
        get(client)
        get(client)
        assert len(downloads) == 2


class TestRateLimit:
    def test_new_analyses_stop_after_the_limit_and_say_when_to_come_back(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_rate_limit=2))
        assert [get(client, f"o/r{n}").status_code for n in range(2)] == [200, 200]
        blocked= get(client, "o/r2")
        assert blocked.status_code == 429
        assert 1 <= int(blocked.headers["retry-after"]) <= 61
        assert len(downloads) == 2

    def test_what_is_already_in_the_cache_is_still_served_and_costs_nothing(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_rate_limit=1))
        assert get(client, "o/a").status_code == 200
        assert get(client, "o/b").status_code == 429
        for _ in range(5):
            assert get(client, "o/a").status_code == 200

    def test_a_bad_address_does_not_use_up_the_allowance(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_rate_limit=1))
        for _ in range(3):
            assert client.get("/dfd", params={"url": "not a url"}).status_code == 400
        assert get(client).status_code == 200

    def test_a_forwarded_address_is_ignored_unless_trusted(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_rate_limit=1))
        assert get(client, "o/a", ).status_code == 200
        blocked= client.get("/dfd", params={"url": "https://github.com/o/b"}, headers={"X-Forwarded-For": "8.8.8.8"})
        assert blocked.status_code == 429  # mudar o cabeçalho não dá outra quota

    def test_behind_a_trusted_proxy_each_forwarded_client_has_its_own_quota(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_rate_limit=1, trust_forwarded_for=True))
        url= {"url": "https://github.com/o/a"}
        assert client.get("/dfd", params=url, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        assert client.get("/dfd", params={"url": "https://github.com/o/b"}, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
        assert client.get("/dfd", params={"url": "https://github.com/o/c"}, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429

    def test_it_can_be_turned_off(self, monkeypatch, downloads):
        client= TestClient(app_with(monkeypatch, dfd_rate_limit=0))
        assert all(get(client, f"o/r{n}").status_code == 200 for n in range(15))


@pytest.mark.anyio
async def test_when_every_slot_is_taken_a_new_analysis_is_turned_away_and_the_slot_comes_back(monkeypatch):
    started= asyncio.Event()
    release= asyncio.Event()

    async def slow(owner, repo):
        started.set()
        await release.wait()
        return archive_of(FILES)

    monkeypatch.setattr(dfd_router, "download_archive", slow)
    app= app_with(monkeypatch, dfd_max_concurrent=1, dfd_queue_timeout_seconds=0.05, dfd_rate_limit=0)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first= asyncio.create_task(client.get("/dfd", params={"url": "https://github.com/o/a"}))
        await started.wait()
        busy= await client.get("/dfd", params={"url": "https://github.com/o/b"})
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "10"
        # o que já está na cache não precisa de vaga
        release.set()
        assert (await first).status_code == 200
        assert (await client.get("/dfd", params={"url": "https://github.com/o/a"})).status_code == 200
        # e a vaga voltou: outra análise nova passa
        assert (await client.get("/dfd", params={"url": "https://github.com/o/b"})).status_code == 200


@pytest.mark.anyio
async def test_two_slots_run_two_analyses_at_once(monkeypatch):
    running= 0
    peak= 0

    async def slow(owner, repo):
        nonlocal running, peak
        running += 1
        peak= max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return archive_of(FILES)

    monkeypatch.setattr(dfd_router, "download_archive", slow)
    app= app_with(monkeypatch, dfd_max_concurrent=2, dfd_queue_timeout_seconds=5, dfd_rate_limit=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses= await asyncio.gather(*[client.get("/dfd", params={"url": f"https://github.com/o/r{n}"}) for n in range(5)])
    assert [r.status_code for r in responses] == [200] * 5
    assert peak == 2
