import asyncio
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.github import MAX_COMMIT_PAGES, WEEKS, WEEK_SECONDS, fetch_activity

TOLERANCE= 0.01


def gh(path, *args):
    return subprocess.run(["gh", "api", *args, path], capture_output=True, text=True, check=True).stdout


def gh_token():
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


def lines(path, jq):
    return [line for line in gh(path, "--paginate", "--jq", jq).splitlines() if line.strip()]


def last_page(path):
    match= re.search(r'page=(\d+)>; rel="last"', gh(path, "-i"))
    return int(match.group(1)) if match else 1


def search_total(repo, kind):
    return json.loads(gh(f"search/issues?q=repo:{repo}+type:{kind}+state:open&per_page=1"))["total_count"]


def optional(call, default=None):
    try:
        return call()
    except subprocess.CalledProcessError:
        return default


def weekly(dates, now, since):
    counts= [0] * WEEKS
    for moment in dates:
        week= int((now - moment).total_seconds() // WEEK_SECONDS)
        if 0 <= week < WEEKS:
            counts[WEEKS - 1 - week]+= 1
    complete= min(WEEKS, int((now - since).total_seconds() // WEEK_SECONDS))
    return counts[WEEKS - complete:] if complete else []


def reference(repo, now, api):
    since_30= (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_90= (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits_90= [json.loads(line) for line in lines(f"repos/{repo}/commits?since={since_90}&per_page=100", '.[] | {l: .author.login, t: .author.type, e: .commit.author.email, d: .commit.committer.date}')]
    people= {c["l"] or c["e"] for c in commits_90 if c["t"]!= "Bot"}
    bots= {c["l"] or c["e"] for c in commits_90 if c["t"]== "Bot"}
    closed= optional(lambda: json.loads(gh(f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100")), [])
    merged= sorted((p for p in closed if p["merged_at"]), key=lambda p: p["merged_at"])
    hours= [(datetime.fromisoformat(p["merged_at"]) - datetime.fromisoformat(p["created_at"])).total_seconds() / 3600 for p in merged]
    info= json.loads(gh(f"repos/{repo}"))
    default= info["default_branch"]
    branch_total= optional(lambda: last_page(f"repos/{repo}/branches?per_page=1"))
    protected= optional(lambda: lines(f"repos/{repo}/branches?protected=true&per_page=100", ".[].name"))
    runs= optional(lambda: json.loads(gh(f"repos/{repo}/actions/runs?branch={default}&status=completed&per_page=1")))
    run= (runs or {}).get("workflow_runs") or []
    sample= commits_90[: MAX_COMMIT_PAGES * 100]
    dates= [datetime.fromisoformat(c["d"]).astimezone(timezone.utc) for c in sample]
    window= api["contributors_window"]
    return {
        "total_commits": last_page(f"repos/{repo}/commits?per_page=1"),
        "commits_30d": last_page(f"repos/{repo}/commits?since={since_30}&per_page=1"),
        "commits_90d": len(commits_90),
        "active_contributors": len(people),
        "bots_excluded": len(bots),
        "median_pr_merge_time": round(statistics.median(hours), 1) if hours else None,
        "open_prs": search_total(repo, "pr"),
        "open_issues": search_total(repo, "issue"),
        "commit_sha": json.loads(gh(f"repos/{repo}/commits?per_page=1"))[0]["sha"],
        "license": (info.get("license") or {}).get("spdx_id"),
        "branches_total": branch_total,
        "protected": protected,
        "default_protected": None if protected is None else default in protected,
        "ci_conclusion": run[0]["conclusion"] if run else None,
        "weekly_commits": weekly(dates, now, window["since"] if not window["complete"] else now - timedelta(days=90)),
        "recent_prs": [{"number": p["number"], "hours": round(h, 1)} for p, h in zip(merged[-10:], hours[-10:])],
    }


def close(api, ref, lower_bound=False):
    if api is None or ref is None:
        return api== ref
    if abs(api - ref) <= max(2, TOLERANCE * ref):
        return True
    return lower_bound and api < ref


def verdict(ok, minimum=False):
    return "MIN" if minimum else ("OK " if ok else "DIF")


def rows(api, ref):
    raw= api["raw_metrics"]
    window= api["contributors_window"]
    branches= api["branches"] or {}
    unknown= ref["protected"] is None or ref["branches_total"] is None or not api["branches"]
    ci= api["ci"] or {}
    partial= not window["complete"]
    list_ok= lambda a, b: len(a)== len(b) and all(abs(x - y) <= max(1, TOLERANCE * y) for x, y in zip(a, b))
    return [
        ("total_commits", api["total_commits"], ref["total_commits"], close(api["total_commits"], ref["total_commits"])),
        ("commits_30d", raw["commits_last_30d"], ref["commits_30d"], close(raw["commits_last_30d"], ref["commits_30d"])),
        ("commits_90d", raw["commits_last_90d"], ref["commits_90d"], close(raw["commits_last_90d"], ref["commits_90d"])),
        ("active_contributors", api["active_contributors"], ref["active_contributors"], close(api["active_contributors"], ref["active_contributors"], partial)),
        ("bots_excluded", api["bots_excluded"], ref["bots_excluded"], close(api["bots_excluded"], ref["bots_excluded"], partial)),
        ("median_pr_merge_time", api["median_pr_merge_time"], ref["median_pr_merge_time"], close(api["median_pr_merge_time"], ref["median_pr_merge_time"])),
        ("open_prs", raw["open_prs"], ref["open_prs"], close(raw["open_prs"], ref["open_prs"])),
        ("open_issues", api["open_issues"], ref["open_issues"], close(api["open_issues"], ref["open_issues"])),
        ("commit_sha", api["commit_sha"], ref["commit_sha"], api["commit_sha"]== ref["commit_sha"]),
        ("license", (api["license"] or {}).get("spdx_id"), ref["license"], (api["license"] or {}).get("spdx_id")== ref["license"]),
        ("branches_total", branches.get("total"), ref["branches_total"], unknown or close(branches.get("total"), ref["branches_total"])),
        ("protected_branches", len(branches.get("protected", [])), None if unknown else len(ref["protected"]), unknown or sorted(branches.get("protected", []))== sorted(ref["protected"][:100]) or close(len(branches.get("protected", [])), len(ref["protected"]))),
        ("default_protected", branches.get("default_protected"), ref["default_protected"], unknown or branches.get("default_protected")== ref["default_protected"]),
        ("ci_conclusion", ci.get("conclusion"), ref["ci_conclusion"], ci.get("conclusion")== ref["ci_conclusion"]),
        ("weekly_commits", api["weekly_commits"], ref["weekly_commits"], list_ok(api["weekly_commits"], ref["weekly_commits"])),
        ("recent_prs", [p["number"] for p in api["recent_prs"]], [p["number"] for p in ref["recent_prs"]], [(p["number"], p["hours"]) for p in api["recent_prs"]]== [(p["number"], p["hours"]) for p in ref["recent_prs"]]),
    ]


def own_checks(api):
    window= api["contributors_window"]
    punch= api["punch_card"]
    checks= [("punch_card_sum_is_commits_read", sum(map(sum, punch)) if punch else 0, window["commits_read"], (sum(map(sum, punch)) if punch else 0)== window["commits_read"])]
    if api["branches"]:
        checks.append(("default_branch_listed", api["default_branch"] in api["branches"]["names"], True, api["default_branch"] in api["branches"]["names"] or api["branches"]["total"] > 100))
    return checks


async def check(repo, token):
    owner, name= repo.split("/")
    now= datetime.now(timezone.utc)
    api= await fetch_activity(owner, name, token)
    ref= reference(repo, now, api)
    print(f"== {repo} (contributors window complete={api['contributors_window']['complete']}, commits read={api['contributors_window']['commits_read']})")
    failed= 0
    for label, value, expected, ok in rows(api, ref) + own_checks(api):
        minimum= not ok and label in ("active_contributors", "bots_excluded") and not api["contributors_window"]["complete"]
        failed+= 0 if ok or minimum else 1
        print(f"  {verdict(ok, minimum)} {label:30} api={str(value)[:60]:60} gh={str(expected)[:60]}")
    return failed


async def main(repos):
    token= gh_token()
    failed= 0
    for repo in repos:
        failed+= await check(repo, token)
    print("FAIL" if failed else "ALL OK", f"({failed} differences)" if failed else "")
    return 1 if failed else 0


if __name__== "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
