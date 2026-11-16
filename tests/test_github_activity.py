from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.github import GitHubError, fetch_activity, get_activity, parse_repo_url
from main import app

from conftest import FakeGitHub, commit, pull

pytestmark= pytest.mark.anyio

NOW= datetime.now(timezone.utc)


def recent_sample():
    return [
        commit("alice", NOW - timedelta(days=1)),
        commit("alice", NOW - timedelta(days=2)),
        commit(None, NOW - timedelta(days=3), email="bob@example.com"),
        commit("carol", NOW - timedelta(days=4)),
        commit("dependabot[bot]", NOW - timedelta(days=5), bot=True),
    ]


async def test_counts_come_from_the_github_totals(install):
    install(FakeGitHub(sample=recent_sample(), closed=[pull(2), pull(10), pull(4), pull(5, merged=False)]))

    data= await fetch_activity("o", "r")

    assert data["total_commits"]== 1200
    assert data["raw_metrics"]["commits_last_30d"]== 40
    assert data["raw_metrics"]["commits_last_90d"]== 300
    assert data["raw_metrics"]["commits_per_week_90d"]== 23.3
    assert data["raw_metrics"]["open_prs"]== 3
    assert data["open_issues"]== 7
    assert data["default_branch"]== "main"


async def test_active_contributors_leave_bots_out(install):
    install(FakeGitHub(sample=recent_sample()))

    data= await fetch_activity("o", "r")

    assert data["active_contributors"]== 3
    assert data["bots_excluded"]== 1
    assert data["contributors_window"]["complete"] is True
    assert data["contributors_window"]["commits_read"]== 5


async def test_window_is_marked_incomplete_when_the_sample_hits_the_cap(install):
    sample= [commit(f"user{i % 7}", NOW - timedelta(hours=i)) for i in range(500)]
    install(FakeGitHub(sample=sample))

    data= await fetch_activity("o", "r")

    window= data["contributors_window"]
    assert window["complete"] is False
    assert window["commits_read"]== 500
    assert window["since"]== NOW - timedelta(hours=499)
    assert data["active_contributors"]== 7


@pytest.mark.parametrize(
    ("hours", "expected"),
    [([2, 10, 4], 4.0), ([2, 4, 6, 8], 5.0), ([7], 7.0), ([1, 1, 500], 1.0)],
)
async def test_merge_time_is_the_median_of_merged_pull_requests(install, hours, expected):
    install(FakeGitHub(closed=[pull(h) for h in hours] + [pull(3, merged=False)]))

    data= await fetch_activity("o", "r")

    assert data["median_pr_merge_time"]== expected
    assert data["raw_metrics"]["merged_prs_sampled"]== len(hours)


async def test_merge_time_is_none_without_merged_pull_requests(install):
    install(FakeGitHub(closed=[pull(3, merged=False)]))

    data= await fetch_activity("o", "r")

    assert data["median_pr_merge_time"] is None
    assert data["raw_metrics"]["merged_prs_sampled"]== 0


async def test_repository_with_pull_requests_disabled_is_not_a_404(install):
    install(FakeGitHub(pulls=False, open_issues_count=4))

    data= await fetch_activity("o", "r")

    assert data["raw_metrics"]["open_prs"]== 0
    assert data["median_pr_merge_time"] is None
    assert data["open_issues"]== 4


async def test_empty_repository_is_a_422(install):
    install(FakeGitHub(empty=True))

    with pytest.raises(GitHubError) as error:
        await fetch_activity("o", "r")

    assert error.value.status== 422


async def test_missing_repository_is_a_404(install):
    install(FakeGitHub(missing=True))

    with pytest.raises(GitHubError) as error:
        await fetch_activity("o", "r")

    assert error.value.status== 404


async def test_rate_limit_is_a_429(install):
    install(FakeGitHub(limited=True))

    with pytest.raises(GitHubError) as error:
        await fetch_activity("o", "r")

    assert error.value.status== 429


async def test_second_request_inside_the_ttl_is_served_from_cache(install):
    fake= install(FakeGitHub(sample=recent_sample()))

    first= await get_activity("o", "r")
    calls_after_first= len(fake.calls)
    second= await get_activity("o", "r")

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(fake.calls)== calls_after_first
    assert second["fetched_at"]== first["fetched_at"]
    assert second["refresh_available_at"]== first["fetched_at"] + timedelta(seconds=settings.activity_cache_ttl_seconds)
    assert second["total_commits"]== first["total_commits"]


async def test_cache_key_ignores_letter_case(install):
    install(FakeGitHub())

    await get_activity("Octo", "Repo")
    again= await get_activity("octo", "repo")

    assert again["cached"] is True


async def test_expired_entry_is_fetched_again(install, monkeypatch):
    fake= install(FakeGitHub())
    monkeypatch.setattr(settings, "activity_cache_ttl_seconds", 0)

    await get_activity("o", "r")
    calls_after_first= len(fake.calls)
    again= await get_activity("o", "r")

    assert again["cached"] is False
    assert len(fake.calls) > calls_after_first


async def test_errors_are_not_cached(install):
    fake= install(FakeGitHub(missing=True))

    with pytest.raises(GitHubError):
        await get_activity("o", "r")
    fake.missing= False
    data= await get_activity("o", "r")

    assert data["cached"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octocat/Hello-World",
        "https://github.com/octocat/Hello-World.git",
        "https://github.com/octocat/Hello-World/",
        "https://github.com/octocat/Hello-World/tree/main/src",
    ],
)
def test_parse_repo_url_accepts_the_urls_the_frontend_sends(url):
    assert parse_repo_url(url)== ("octocat", "Hello-World")


@pytest.mark.parametrize(
    "url",
    ["https://github.com/octocat", "https://github.com/", "https://gitlab.com/a/b", "not a url"],
)
def test_parse_repo_url_rejects_other_urls(url):
    with pytest.raises(GitHubError) as error:
        parse_repo_url(url)

    assert error.value.status== 400


def test_endpoint_returns_the_new_fields(install):
    install(FakeGitHub(sample=recent_sample(), closed=[pull(2), pull(4)]))

    response= TestClient(app).get("/github/activity", params={"url": "https://github.com/o/r"})

    body= response.json()
    assert response.status_code== 200
    assert body["median_pr_merge_time"]== 3.0
    assert body["active_contributors"]== 3
    assert body["bots_excluded"]== 1
    assert body["cached"] is False
    assert {"fetched_at", "refresh_available_at", "contributors_window"} <= body.keys()


def test_endpoint_maps_an_empty_repository_to_422(install):
    install(FakeGitHub(empty=True))

    response= TestClient(app).get("/github/activity", params={"url": "https://github.com/o/r"})

    assert response.status_code== 422
    assert response.json()["detail"]== "Repositório vazio"
