from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.services.github import WEEKS, fetch_activity, punch_card, recent_prs, weekly_commits
from main import app

from conftest import FakeGitHub, commit, pull

pytestmark= pytest.mark.anyio

NOW= datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
SHA= "a3f89c1d" * 5


def at(days, hour=12):
    return NOW - timedelta(days=days) + timedelta(hours=hour - 12)


async def test_head_commit_comes_from_the_total_request_without_an_extra_call(install):
    fake= install(FakeGitHub())

    data= await fetch_activity("o", "r")

    assert data["commit_sha"]== SHA
    assert len([call for call in fake.calls if call.url.path.endswith("/commits") and call.url.params.get("per_page")== "1" and "since" not in call.url.params])== 1


async def test_license_is_read_from_the_repository_info(install):
    install(FakeGitHub(license_="Apache-2.0"))

    data= await fetch_activity("o", "r")

    assert data["license"]== {"spdx_id": "Apache-2.0", "name": "Apache-2.0 License"}


async def test_a_repository_without_license_has_none(install):
    install(FakeGitHub(license_=None))

    assert (await fetch_activity("o", "r"))["license"] is None


async def test_branches_report_names_total_and_protection(install):
    install(FakeGitHub(branches=[{"name": "main", "protected": True}, {"name": "dev", "protected": False}, {"name": "release", "protected": True}]))

    data= await fetch_activity("o", "r")

    assert data["branches"]== {"names": ["main", "dev", "release"], "total": 3, "protected": ["main", "release"], "default_protected": True}


async def test_default_branch_not_protected(install):
    install(FakeGitHub(branches=[{"name": "main", "protected": False}, {"name": "stable", "protected": True}]))

    data= await fetch_activity("o", "r")

    assert data["branches"]["default_protected"] is False and data["branches"]["protected"]== ["stable"]


async def test_more_than_a_hundred_branches_use_the_total_and_the_protected_filter(install):
    many= [{"name": "main", "protected": True}] + [{"name": f"b{i}", "protected": i % 50== 0} for i in range(1, 150)]
    fake= install(FakeGitHub(branches=many))

    data= await fetch_activity("o", "r")

    assert data["branches"]["total"]== 150
    assert len(data["branches"]["names"])== 100
    assert data["branches"]["protected"][0]== "main" and "b50" in data["branches"]["protected"] and "b100" in data["branches"]["protected"]
    assert any(call.url.params.get("protected")== "true" for call in fake.calls)


@pytest.mark.parametrize("conclusion,passing", [("success", True), ("failure", False), ("timed_out", False), ("cancelled", None)])
async def test_ci_reports_the_latest_completed_run(install, conclusion, passing):
    install(FakeGitHub(ci=conclusion))

    data= await fetch_activity("o", "r")

    assert data["ci"]== {"provider": "GitHub Actions", "conclusion": conclusion, "passing": passing, "ran_at": "2026-10-20T10:00:00Z"}


@pytest.mark.parametrize("options", [{"ci": None}, {"ci": "missing"}, {"actions_forbidden": True}])
async def test_ci_is_none_without_runs_or_access(install, options):
    install(FakeGitHub(**options))

    assert (await fetch_activity("o", "r"))["ci"] is None


async def test_optional_blocks_do_not_break_the_analysis_when_github_refuses(install):
    install(FakeGitHub(actions_forbidden=True, ci="missing"))

    data= await fetch_activity("o", "r")

    assert data["total_commits"]== 1200 and data["ci"] is None


async def test_the_run_stays_in_a_normal_number_of_requests(install):
    fake= install(FakeGitHub(sample=[commit("ana", datetime.now(timezone.utc) - timedelta(days=3))], closed=[pull(2)]))

    await fetch_activity("o", "r")

    assert len(fake.calls) <= 12


def test_weekly_commits_bucket_by_full_weeks_oldest_first():
    sample= [commit("a", at(2)), commit("a", at(3)), commit("a", at(9)), commit("a", at(30)), commit("a", at(83)), commit("a", at(86))]

    weeks= weekly_commits(sample, NOW, NOW - timedelta(days=90))

    assert len(weeks)== WEEKS
    assert (weeks[11], weeks[10], weeks[7], weeks[0])== (2, 1, 1, 1)
    assert sum(weeks)== 5


def test_weekly_commits_drop_weeks_outside_a_truncated_window():
    sample= [commit("a", at(i * 0.08)) for i in range(500)]
    oldest= at(40)

    weeks= weekly_commits(sample, NOW, oldest)

    assert len(weeks)== 5
    assert sum(weeks)== len([i for i in range(500) if i * 0.08 < 35])


def test_weekly_commits_without_commits_are_zeros():
    assert weekly_commits([], NOW, NOW - timedelta(days=90))== [0] * WEEKS


def test_punch_card_uses_sunday_as_day_zero_and_utc_hours():
    sample= [
        commit("a", datetime(2026, 11, 15, 14, 5, tzinfo=timezone.utc)),
        commit("a", datetime(2026, 11, 16, 0, 59, tzinfo=timezone.utc)),
        commit("a", datetime(2026, 11, 16, 0, 1, tzinfo=timezone.utc)),
        commit("a", datetime(2026, 11, 21, 23, 0, tzinfo=timezone.utc)),
    ]

    grid= punch_card(sample)

    assert len(grid)== 7 and all(len(day)== 24 for day in grid)
    assert grid[0][14]== 1
    assert grid[1][0]== 2
    assert grid[6][23]== 1
    assert sum(sum(day) for day in grid)== len(sample)


def test_punch_card_is_none_without_commits():
    assert punch_card([]) is None


def test_recent_prs_are_the_latest_merged_in_chronological_order():
    closed= [pull(3, day=5), pull(1, merged=False, day=6), pull(7.25, day=2), pull(2, day=9)]
    numbers= [p["number"] for p in closed]

    result= recent_prs(closed)

    assert [p["number"] for p in result]== [numbers[2], numbers[0], numbers[3]]
    assert [p["hours"] for p in result]== [7.2, 3.0, 2.0]


def test_recent_prs_keep_only_the_last_ten():
    closed= [pull(1, day=i) for i in range(1, 16)]

    result= recent_prs(closed)

    assert len(result)== 10
    assert result[-1]["number"]== closed[-1]["number"]


async def test_activity_returns_the_new_blocks_end_to_end(install, signed_in):
    sample= [commit("ana", datetime.now(timezone.utc) - timedelta(days=5)), commit("bia", datetime.now(timezone.utc) - timedelta(days=20))]
    install(FakeGitHub(sample=sample, closed=[pull(2), pull(4)]))

    response= TestClient(app).get("/github/activity", params={"url": "https://github.com/o/r"})

    body= response.json()
    assert response.status_code== 200
    assert body["commit_sha"]== SHA
    assert body["license"]["spdx_id"]== "MIT"
    assert body["branches"]["default_protected"] is True
    assert body["ci"]["passing"] is True
    assert len(body["weekly_commits"])== WEEKS and sum(body["weekly_commits"])== 2
    assert sum(sum(day) for day in body["punch_card"])== 2
    assert [p["hours"] for p in body["recent_prs"]]== [2.0, 4.0]
