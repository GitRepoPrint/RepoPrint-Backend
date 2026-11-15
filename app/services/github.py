import asyncio
import base64
import re
import statistics
from datetime import datetime, timedelta, timezone
import httpx
from app.config import settings
from app.services.codeowners import count_owned, parse as parse_codeowners

MAX_COMMIT_PAGES= 5
WEEKS= 12
RECENT_PRS= 10
MAX_CONTRIBUTORS= 10
MAX_TREE_FILES= 60000
CODEOWNERS_PATHS= (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
WEEK_SECONDS= 7 * 24 * 3600
CI_PASSING= {"success": True, "failure": False, "timed_out": False, "startup_failure": False}

REPO_RE= re.compile(r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?(?:/.*)?$")


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        self.status= status
        self.message= message
        
        
def parse_repo_url(url: str) -> tuple[str, str]:
    match= REPO_RE.match(url.strip())
    if not match:
        raise GitHubError(400, "URL não é válido")
    return match.group(1), match.group(2)


def _headers(token: str | None= None) -> dict:
    headers= {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token= token or settings.github_token
    if token:
        headers["Authorization"]= f"Bearer {token}"
    return headers


async def _get(client: httpx.AsyncClient, path: str, **params) -> httpx.Response:
    response= await client.get(path, params=params or None)

    if response.status_code== 401:
        raise GitHubError(401, "Token do GitHub inválido")
    if response.status_code== 404:
        raise GitHubError(404, "Repositório não encontrado ou privado")
    if response.status_code== 409:
        raise GitHubError(422, "Repositório vazio")
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining")== "0":
        raise GitHubError(429, "Rate limit do GitHub excedido")
    if response.is_error:
        raise GitHubError(502, f"GitHub respondeu {response.status_code}")
    return response


async def _count(client: httpx.AsyncClient, path: str, **params) -> int:
    response= await _get(client, path, per_page=1, **params)
    last= response.links.get("last")
    if last:
        return int(re.search(r"[?&]page=(\d+)", last["url"]).group(1))
    return len(response.json())


async def _count_head(client: httpx.AsyncClient, path: str, **params) -> tuple[int, str | None]:
    response= await _get(client, path, per_page=1, **params)
    items= response.json()
    head= items[0].get("sha") if items else None
    last= response.links.get("last")
    if last:
        return int(re.search(r"[?&]page=(\d+)", last["url"]).group(1)), head
    return len(items), head


async def _paginate(client: httpx.AsyncClient, path: str, max_pages: int, **params) -> list[dict]:
    items: list[dict]= []
    url, query= path, {"per_page": 100, **params}
    
    for _ in range(max_pages):
        response= await _get(client, url, **query)
        items += response.json()
        next_page= response.links.get("next")
        if not next_page:
            break
        url, query= next_page["url"], {}
    return items


async def _or_none(call):
    try:
        return await call
    except GitHubError as e:
        if e.status== 404:
            return None
        raise


async def _optional(call):
    try:
        return await call
    except GitHubError as e:
        if e.status in (404, 502):
            return None
        raise


async def _branches(client: httpx.AsyncClient, base: str, default: str) -> dict:
    response= await _get(client, f"{base}/branches", per_page=100)
    items= response.json()
    more= bool(response.links.get("next"))
    total= await _count(client, f"{base}/branches") if more else len(items)
    if more:
        protected= [b["name"] for b in await _paginate(client, f"{base}/branches", 1, protected="true")]
    else:
        protected= [b["name"] for b in items if b.get("protected")]
    return {
        "names": [b["name"] for b in items],
        "total": total,
        "protected": protected,
        "default_protected": default in protected,
    }


async def _file_text(client: httpx.AsyncClient, base: str, path: str, ref: str) -> str | None:
    data= (await _get(client, f"{base}/contents/{path}", ref=ref)).json()
    if not isinstance(data, dict) or data.get("encoding")!= "base64":
        return None
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


async def _code_owners(client: httpx.AsyncClient, base: str, sha: str | None) -> dict | None:
    if not sha:
        return None
    tree= (await _get(client, f"{base}/git/trees/{sha}", recursive=1)).json()
    if tree.get("truncated"):
        return None
    paths= [entry["path"] for entry in tree.get("tree") or [] if entry.get("type")== "blob"]
    if len(paths) > MAX_TREE_FILES:
        return None
    present= set(paths)
    location= next((path for path in CODEOWNERS_PATHS if path in present), None)
    if location is None:
        return {"file": None, "coverage": 0.0, "files_total": len(paths), "files_owned": 0}
    text= await _file_text(client, base, location, sha)
    if text is None:
        return None
    owned= await asyncio.to_thread(count_owned, paths, parse_codeowners(text))
    return {"file": location, "coverage": round(owned / len(paths) * 100, 1) if paths else 0.0, "files_total": len(paths), "files_owned": owned}


async def _ci(client: httpx.AsyncClient, base: str, branch: str) -> dict | None:
    response= await _get(client, f"{base}/actions/runs", branch=branch, status="completed", per_page=1)
    runs= response.json().get("workflow_runs") or []
    if not runs:
        return None
    conclusion= runs[0].get("conclusion")
    return {
        "provider": "GitHub Actions",
        "conclusion": conclusion,
        "passing": CI_PASSING.get(conclusion),
        "ran_at": runs[0].get("updated_at"),
    }


def _committed(commit: dict) -> datetime:
    return _dt(commit["commit"]["committer"]["date"]).astimezone(timezone.utc)


def weekly_commits(sample: list[dict], now: datetime, since: datetime) -> list[int]:
    counts= [0] * WEEKS
    for commit in sample:
        week= int((now - _committed(commit)).total_seconds() // WEEK_SECONDS)
        if 0 <= week < WEEKS:
            counts[WEEKS - 1 - week]+= 1
    complete= min(WEEKS, int((now - since).total_seconds() // WEEK_SECONDS))
    return counts[WEEKS - complete:] if complete else []


def punch_card(sample: list[dict]) -> list[list[int]] | None:
    if not sample:
        return None
    grid= [[0] * 24 for _ in range(7)]
    for commit in sample:
        moment= _committed(commit)
        grid[(moment.weekday() + 1) % 7][moment.hour]+= 1
    return grid


def contributor_ranking(sample: list[dict]) -> list[dict]:
    people: dict[str, dict]= {}
    for item in sample:
        if _is_bot(item):
            continue
        moment= _committed(item)
        entry= people.setdefault(_author(item), {"login": (item["author"] or {}).get("login"), "commits": 0, "last_commit_at": moment})
        entry["commits"]+= 1
        entry["last_commit_at"]= max(entry["last_commit_at"], moment)
    ranked= sorted(people.values(), key=lambda p: (-p["commits"], -p["last_commit_at"].timestamp()))
    return ranked[:MAX_CONTRIBUTORS]


def recent_prs(closed: list[dict]) -> list[dict]:
    merged= sorted((p for p in closed if p["merged_at"]), key=lambda p: p["merged_at"])
    return [
        {"number": p["number"], "hours": round((_dt(p["merged_at"]) - _dt(p["created_at"])).total_seconds() / 3600, 1)}
        for p in merged[-RECENT_PRS:]
        if "number" in p
    ]


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _author(commit: dict) -> str:
    return (commit["author"] or {}).get("login") or commit["commit"]["author"]["email"]


def _is_bot(commit: dict) -> bool:
    return (commit["author"] or {}).get("type")== "Bot"


async def fetch_repository(owner: str, repo: str, token: str | None= None) -> dict:
    async with httpx.AsyncClient(base_url=settings.github_api_url, headers=_headers(token), timeout=15, follow_redirects=True) as client:
        info= (await _get(client, f"/repos/{owner}/{repo}")).json()
    if info.get("private") and not token:
        raise GitHubError(404, "Repositório não encontrado ou privado")
    return {"full_name": info["full_name"], "private": bool(info.get("private")), "default_branch": info.get("default_branch")}


async def fetch_activity(owner: str, repo: str, token: str | None= None) -> dict:
    now= datetime.now(timezone.utc)
    since_90= now - timedelta(days=90)
    since_30= now - timedelta(days=30)
    base= f"/repos/{owner}/{repo}"

    async with httpx.AsyncClient(base_url=settings.github_api_url, headers=_headers(token), timeout=15, follow_redirects=True) as client:
        info, total_head, commits_30d, commits_90d, open_prs, commit_sample, closed_prs= await asyncio.gather(
            _get(client, base),
            _count_head(client, f"{base}/commits"),
            _count(client, f"{base}/commits", since=since_30.isoformat()),
            _count(client, f"{base}/commits", since=since_90.isoformat()),
            _or_none(_count(client, f"{base}/pulls", state="open")),
            _paginate(client, f"{base}/commits", MAX_COMMIT_PAGES, since=since_90.isoformat()),
            _or_none(_paginate(client, f"{base}/pulls", 1, state="closed", sort="updated", direction="desc")),
        )

        info= info.json()
        if info.get("private") and not token:
            raise GitHubError(404, "Repositório não encontrado ou privado")
        open_prs= open_prs or 0
        closed_prs= closed_prs or []
        total_commits, head_sha= total_head
        branches, ci, code_owners= await asyncio.gather(
            _optional(_branches(client, base, info["default_branch"])),
            _optional(_ci(client, base, info["default_branch"])),
            _optional(_code_owners(client, base, head_sha)),
        )


    merge_hours= [
        (_dt(p["merged_at"]) - _dt(p["created_at"])).total_seconds() / 3600
        for p in closed_prs
        if p["merged_at"]
        ]

    people= {_author(c) for c in commit_sample if not _is_bot(c)}
    bots= {_author(c) for c in commit_sample if _is_bot(c)}
    truncated= len(commit_sample) >= MAX_COMMIT_PAGES * 100
    window_start= min(_dt(c["commit"]["committer"]["date"]) for c in commit_sample) if truncated else since_90

    return {
        "owner": owner,
        "repo": repo,
        "default_branch": info["default_branch"],
        "total_commits": total_commits,
        "active_contributors": len(people),
        "contributors_window": {"complete": not truncated, "commits_read": len(commit_sample), "since": window_start},
        "bots_excluded": len(bots),
        "median_pr_merge_time": round(statistics.median(merge_hours), 1) if merge_hours else None,
        # open_issues_count do GitHub inclui PRs abertos
        "open_issues": max(info["open_issues_count"] - open_prs, 0),
        "commit_sha": head_sha,
        "license": {"spdx_id": info["license"].get("spdx_id"), "name": info["license"].get("name")} if info.get("license") else None,
        "branches": branches,
        "ci": ci,
        "weekly_commits": weekly_commits(commit_sample, now, window_start),
        "punch_card": punch_card(commit_sample),
        "recent_prs": recent_prs(closed_prs),
        "contributors": contributor_ranking(commit_sample),
        "code_owners": code_owners,
        "raw_metrics": {
            "commits_last_30d": commits_30d,
            "commits_last_90d": commits_90d,
            "commits_per_week_90d": round(commits_90d / (90 / 7), 1),
            "open_prs": open_prs,
            "merged_prs_sampled": len(merge_hours),
            "stars": info["stargazers_count"],
        },
    }


_cache: dict[str, tuple[datetime, dict]]= {}


async def get_activity(owner: str, repo: str, token: str | None= None, scope: str | None= None) -> dict:
    now= datetime.now(timezone.utc)
    ttl= timedelta(seconds=settings.activity_cache_ttl_seconds)
    for expired in [k for k, (stamp, _) in _cache.items() if now - stamp >= ttl]:
        del _cache[expired]

    key= f"{owner}/{repo}".lower()
    if token:
        key= f"{key}@{scope}"
    if key in _cache:
        stamp, data= _cache[key]
        return {**data, "fetched_at": stamp, "refresh_available_at": stamp + ttl, "cached": True}

    data= await fetch_activity(owner, repo, token)
    _cache[key]= (now, data)
    return {**data, "fetched_at": now, "refresh_available_at": now + ttl, "cached": False}
