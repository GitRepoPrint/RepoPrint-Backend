import pytest

from app.services.codeowners import compile_pattern, count_owned, parse
from app.services.github import MAX_TREE_FILES, fetch_activity

from conftest import FakeGitHub

pytestmark= pytest.mark.anyio

FILES= ["README.md", "LICENSE", "src/app.py", "src/util/helpers.py", "src/util/deep/more.py", "docs/index.md", "docs/guide/setup.md", "web/app.js", "web/lib/a.js", ".github/workflows/ci.yml"]


def owned(text, files=FILES):
    rules= parse(text)
    return [path for path in files if count_owned([path], rules)]


def test_a_star_owns_everything():
    assert owned("* @team") == FILES


def test_the_last_matching_rule_wins():
    assert owned("* @team\n/docs/ @docs") == FILES
    assert owned("* @team\n/docs/") == [path for path in FILES if not path.startswith("docs/")]


def test_a_directory_with_a_trailing_slash_owns_everything_inside():
    assert owned("/src/ @a") == ["src/app.py", "src/util/helpers.py", "src/util/deep/more.py"]


def test_an_unanchored_directory_matches_at_any_depth():
    assert owned("util/ @a") == ["src/util/helpers.py", "src/util/deep/more.py"]


def test_a_star_directory_pattern_does_not_descend():
    assert owned("/docs/* @a") == ["docs/index.md"]
    assert owned("docs/* @a") == ["docs/index.md"]


def test_double_star_crosses_directories():
    assert owned("/docs/** @a") == ["docs/index.md", "docs/guide/setup.md"]
    assert owned("/src/**/more.py @a") == ["src/util/deep/more.py"]
    assert owned("**/helpers.py @a") == ["src/util/helpers.py"]


def test_extension_patterns_match_at_any_depth():
    assert owned("*.js @a") == ["web/app.js", "web/lib/a.js"]
    assert owned("/*.md @a") == ["README.md"]


def test_a_plain_name_matches_a_file_or_a_directory():
    assert owned("LICENSE @a") == ["LICENSE"]
    assert owned("/web @a") == ["web/app.js", "web/lib/a.js"]


def test_a_rule_without_owners_removes_ownership():
    assert owned("* @team\n/src/util/") == [path for path in FILES if not path.startswith("src/util/")]


def test_comments_blank_lines_and_inline_comments_are_ignored():
    text= "# header\n\n/src/ @a # the app\n   # indented comment\n"

    assert owned(text) == ["src/app.py", "src/util/helpers.py", "src/util/deep/more.py"]


def test_emails_and_teams_count_as_owners():
    assert owned("/docs/ docs@example.com\n/web/ @org/frontend") == ["docs/index.md", "docs/guide/setup.md", "web/app.js", "web/lib/a.js"]


def test_unsupported_patterns_are_skipped():
    assert owned("!/src/ @a\n[ab].py @a\n/docs/ @a") == ["docs/index.md", "docs/guide/setup.md"]
    assert compile_pattern("!x") is None and compile_pattern("/") is None


def test_an_empty_file_owns_nothing():
    assert owned("") == []


async def test_coverage_is_the_share_of_files_with_an_owner(install):
    install(FakeGitHub(files=["a.py", "b.py", "src/c.py", "src/d.py"], codeowners="/src/ @dev\n"))

    data= await fetch_activity("o", "r")

    assert data["code_owners"]== {"file": ".github/CODEOWNERS", "coverage": 40.0, "files_total": 5, "files_owned": 2}


async def test_the_codeowners_file_counts_as_a_file(install):
    install(FakeGitHub(files=["a.py"], codeowners="* @dev\n"))

    assert (await fetch_activity("o", "r"))["code_owners"]["coverage"]== 100.0


@pytest.mark.parametrize("location", [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"])
async def test_the_three_github_locations_are_found(install, location):
    install(FakeGitHub(files=["a.py"], codeowners="* @dev\n", codeowners_path=location))

    assert (await fetch_activity("o", "r"))["code_owners"]["file"]== location


async def test_a_repository_without_the_file_has_zero_coverage_and_no_file(install):
    fake= install(FakeGitHub(files=["a.py", "b.py"]))

    data= await fetch_activity("o", "r")

    assert data["code_owners"]== {"file": None, "coverage": 0.0, "files_total": 2, "files_owned": 0}
    assert not any("/contents/" in call.url.path for call in fake.calls)


async def test_a_truncated_tree_is_not_available(install):
    install(FakeGitHub(codeowners="* @dev\n", tree_truncated=True))

    assert (await fetch_activity("o", "r"))["code_owners"] is None


async def test_a_huge_tree_is_not_available(install):
    install(FakeGitHub(files=[f"f{i}" for i in range(MAX_TREE_FILES + 1)], codeowners="* @dev\n"))

    assert (await fetch_activity("o", "r"))["code_owners"] is None


async def test_a_missing_tree_does_not_break_the_analysis(install):
    install(FakeGitHub(tree_missing=True))

    data= await fetch_activity("o", "r")

    assert data["code_owners"] is None and data["total_commits"]== 1200
