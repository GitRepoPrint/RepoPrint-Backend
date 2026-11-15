import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.github import GitHubError, fetch_activity, review_participation

from conftest import FakeGitHub, commit

pytestmark= pytest.mark.anyio


def prs(*items):
    return [{"author": {"login": author}, "reviews": {"nodes": [{"author": {"login": r}} for r in reviewers]}} for author, reviewers in items]


def test_participation_is_the_share_of_other_peoples_prs_reviewed():
    sample= prs(("ana", ["bia"]), ("ana", []), ("bia", ["ana"]), ("caio", ["ana", "bia"]))

    assert review_participation(sample, "ana")== 100.0
    assert review_participation(sample, "bia")== round(2 / 3 * 100, 1)
    assert review_participation(sample, "caio")== 0.0


def test_own_pull_requests_do_not_count_and_a_second_review_counts_once():
    sample= prs(("ana", ["ana", "ana"]), ("bia", ["ana", "ana", "ana"]))

    assert review_participation(sample, "ana")== 100.0


def test_a_ghost_author_counts_as_somebody_elses_pr():
    sample= [{"author": None, "reviews": {"nodes": [{"author": None}, {"author": {"login": "ana"}}]}}]

    assert review_participation(sample, "ana")== 100.0


def test_without_other_prs_there_is_nothing_to_measure():
    assert review_participation(prs(("ana", ["bia"])), "ana") is None
    assert review_participation([], "ana") is None


def recent(name, days=1):
    return commit(name, datetime.now(timezone.utc) - timedelta(days=days))


async def test_contributors_get_their_review_participation(install):
    fake= install(FakeGitHub(sample=[recent("ana"), recent("ana", 2), recent("bia", 3)], reviews=[
        {"author": "caio", "reviewers": ["ana"]},
        {"author": "caio", "reviewers": ["ana", "bia"]},
        {"author": "bia", "reviewers": []},
        {"author": "ana", "reviewers": ["bia"]},
    ]))

    data= await fetch_activity("o", "r", "token")

    shares= {p["login"]: p["review_participation"] for p in data["contributors"]}
    assert shares== {"ana": round(2 / 3 * 100, 1), "bia": round(2 / 3 * 100, 1)}
    assert data["reviews_sampled"]== 4
    graphql= [c for c in fake.calls if c.url.path.endswith("/graphql")]
    assert len(graphql)== 1
    assert json.loads(graphql[0].content)["variables"]== {"owner": "o", "name": "r", "prs": 50, "reviews": 30}


async def test_unlinked_authors_have_no_participation(install):
    install(FakeGitHub(sample=[commit(None, datetime.now(timezone.utc) - timedelta(days=1))], reviews=[{"author": "caio", "reviewers": ["ana"]}]))

    data= await fetch_activity("o", "r", "token")

    assert data["contributors"][0]["review_participation"] is None


async def test_without_a_token_graphql_is_not_called_and_the_data_is_not_available(install):
    fake= install(FakeGitHub(sample=[recent("ana")], reviews=[{"author": "caio", "reviewers": ["ana"]}]))

    data= await fetch_activity("o", "r")

    assert not any(c.url.path.endswith("/graphql") for c in fake.calls)
    assert data["contributors"][0]["review_participation"] is None and data["reviews_sampled"]== 0


@pytest.mark.parametrize("options", [{"graphql_status": 502}, {"graphql_status": 403}, {"graphql_errors": True}])
async def test_a_graphql_failure_does_not_break_the_analysis(install, options):
    install(FakeGitHub(sample=[recent("ana")], reviews=[{"author": "caio", "reviewers": ["ana"]}], **options))

    data= await fetch_activity("o", "r", "token")

    assert data["total_commits"]== 1200
    assert data["contributors"][0]["review_participation"] is None


async def test_a_rejected_token_is_reported(install):
    install(FakeGitHub(sample=[recent("ana")], graphql_status=401))

    with pytest.raises(GitHubError) as error:
        await fetch_activity("o", "r", "token")

    assert error.value.status== 401
