import asyncio
import re
from datetime import datetime, timedelta, timezone
import httpx
from app.config import settings

API= "https://api.github.com"
MAX_COMMIT_PAGES= 5

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


def _headers() -> dict:
    headers= {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"]= f"Bearer {settings.github_token}"
    return headers


async def _get(client: httpx.AsyncClient, path: str, **params) -> httpx.Response:
    response= await client.get(path, params=params or None)
    
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


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _author(commit: dict) -> str:
    return (commit["author"] or {}).get("login") or commit["commit"]["author"]["email"]


async def fetch_activity(owner: str, repo: str) -> dict:
    now= datetime.now(timezone.utc)
    since_90= now - timedelta(days=90)
    since_30= now - timedelta(days=30)
    base= f"/repos/{owner}/{repo}"
    
    async with httpx.AsyncClient(base_url=API, headers=_headers(), timeout=15, follow_redirects=True) as client:
        info, total_commits, open_prs, commits_90, closed_prs= await asyncio.gather(
            _get(client, base),
            _count(client, f"{base}/commits"),
            _count(client, f"{base}/pulls", state="open"),
            _paginate(client, f"{base}/commits", MAX_COMMIT_PAGES, since=since_90.isoformat()),
            _paginate(client, f"{base}/pulls", 1, state="closed", sort="updated", direction="desc"),
        )
        
        info= info.json()
        
        commits_30= [c for c in commits_90 if _dt(c["commit"]["author"]["date"]) >= since_30]

    merge_hours= [
        (_dt(p["merged_at"]) - _dt(p["created_at"])).total_seconds() / 3600
        for p in closed_prs
        if p["merged_at"]
        ]
    
    return {
        "owner": owner,
        "repo": repo,
        "default_branch": info["default_branch"],
        "total_commits": total_commits,
        "active_contributors": len({_author(c) for c in commits_90}),
        "avg_pr_merge_time": round(sum(merge_hours) / len(merge_hours), 1) if merge_hours else None,
        # open_issues_count do GitHub inclui PRs abertos
        "open_issues": max(info["open_issues_count"] - open_prs, 0),
        "raw_metrics": {
            "commits_last_30d": len(commits_30),
            "commits_last_90d": len(commits_90),
            "commits_per_week_90d": round(len(commits_90) / (90 / 7), 1),
            "commits_window_truncated": len(commits_90) >= MAX_COMMIT_PAGES * 100,
            "open_prs": open_prs,
            "merged_prs_sampled": len(merge_hours),
            "stars": info["stargazers_count"],
        },
    }
