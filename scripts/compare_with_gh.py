import json
import os
import re
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BACKEND= os.environ.get("BACKEND_URL", "http://localhost:8000")


def gh(path, *args):
    return subprocess.run(["gh", "api", *args, path], capture_output=True, text=True, check=True).stdout


def lines(path, jq):
    return [line for line in gh(path, "--paginate", "--jq", jq).splitlines() if line.strip()]


def backend(repo):
    url= urllib.parse.quote(f"https://github.com/{repo}", safe="")
    with urllib.request.urlopen(f"{BACKEND}/github/activity?url={url}", timeout=120) as response:
        return json.load(response)


def last_page(path):
    match= re.search(r'page=(\d+)>; rel="last"', gh(path, "-i"))
    return int(match.group(1)) if match else 1


def search_total(repo, kind):
    return json.loads(gh(f"search/issues?q=repo:{repo}+type:{kind}+state:open&per_page=1"))["total_count"]


def reference(repo):
    now= datetime.now(timezone.utc)
    since_30= (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_90= (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits_90= lines(f"repos/{repo}/commits?since={since_90}&per_page=100", '.[] | {l: .author.login, t: .author.type, e: .commit.author.email}')
    commits_90= [json.loads(line) for line in commits_90]
    people= {c["l"] or c["e"] for c in commits_90 if c["t"]!= "Bot"}
    bots= {c["l"] or c["e"] for c in commits_90 if c["t"]== "Bot"}
    try:
        closed= json.loads(gh(f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100"))
    except subprocess.CalledProcessError:
        closed= []
    hours= [
        (datetime.fromisoformat(p["merged_at"]) - datetime.fromisoformat(p["created_at"])).total_seconds() / 3600
        for p in closed
        if p["merged_at"]
    ]
    return {
        "total_commits": last_page(f"repos/{repo}/commits?per_page=1"),
        "commits_30d": last_page(f"repos/{repo}/commits?since={since_30}&per_page=1"),
        "commits_90d": len(commits_90),
        "active_contributors": len(people),
        "bots_excluded": len(bots),
        "median_pr_merge_time": round(statistics.median(hours), 1) if hours else None,
        "open_prs": search_total(repo, "pr"),
        "open_issues": search_total(repo, "issue"),
    }


def verdict(api, ref, lower_bound=False):
    if api is None or ref is None:
        return "OK " if api== ref else "DIF"
    if abs(api - ref) <= max(2, 0.01 * ref):
        return "OK "
    if lower_bound and api < ref:
        return "MIN"
    return "DIF"


def main(repos):
    for repo in repos:
        api= backend(repo)
        ref= reference(repo)
        raw= api["raw_metrics"]
        window= api["contributors_window"]
        rows= [
            ("total_commits", api["total_commits"], ref["total_commits"], False),
            ("commits_30d", raw["commits_last_30d"], ref["commits_30d"], False),
            ("commits_90d", raw["commits_last_90d"], ref["commits_90d"], False),
            ("active_contributors", api["active_contributors"], ref["active_contributors"], not window["complete"]),
            ("bots_excluded", api["bots_excluded"], ref["bots_excluded"], not window["complete"]),
            ("median_pr_merge_time", api["median_pr_merge_time"], ref["median_pr_merge_time"], False),
            ("open_prs", raw["open_prs"], ref["open_prs"], False),
            ("open_issues", api["open_issues"], ref["open_issues"], False),
        ]
        print(f"== {repo} (cached={api['cached']}, contributors window complete={window['complete']}, commits read={window['commits_read']})")
        for name, value, expected, lower_bound in rows:
            print(f"  {verdict(value, expected, lower_bound)} {name:22} api={value!s:10} gh={expected}")


if __name__== "__main__":
    main(sys.argv[1:])
