import os
import subprocess

import pytest

from app.services.github import WEEKS, fetch_activity

pytestmark= [
    pytest.mark.anyio,
    pytest.mark.github_live,
    pytest.mark.skipif(os.environ.get("RUN_GITHUB_LIVE")!= "1", reason="set RUN_GITHUB_LIVE=1 to hit the real GitHub"),
]

REPOS= [("octocat", "Hello-World"), ("pallets", "click")]


@pytest.fixture(scope="module")
def token():
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.parametrize("owner,repo", REPOS)
async def test_real_repository_is_coherent(owner, repo, token):
    data= await fetch_activity(owner, repo, token)

    window= data["contributors_window"]
    assert data["total_commits"] >= data["raw_metrics"]["commits_last_90d"] >= data["raw_metrics"]["commits_last_30d"] - 2
    assert data["commit_sha"] and len(data["commit_sha"])== 40
    assert data["branches"]["total"] >= 1
    assert data["default_branch"] in data["branches"]["names"]
    assert data["branches"]["default_protected"]== (data["default_branch"] in data["branches"]["protected"])
    assert sum(map(sum, data["punch_card"] or [[0]]))== window["commits_read"]
    assert len(data["weekly_commits"]) <= WEEKS
    assert sum(data["weekly_commits"]) <= window["commits_read"]
    assert len(data["recent_prs"]) <= 10
    assert all(p["hours"] >= 0 for p in data["recent_prs"])
    if data["ci"]:
        assert data["ci"]["provider"]== "GitHub Actions"


async def test_known_public_license_and_missing_repository(token):
    click= await fetch_activity("pallets", "click", token)
    assert click["license"]["spdx_id"]== "BSD-3-Clause"
    with pytest.raises(Exception) as error:
        await fetch_activity("pallets", "does-not-exist-repoprint", token)
    assert getattr(error.value, "status", None)== 404
