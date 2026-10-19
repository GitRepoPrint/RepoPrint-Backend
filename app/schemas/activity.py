from pydantic import BaseModel

class ActivityMetrics(BaseModel):
    owner: str
    repo: str
    default_branch: str
    total_commits: int
    active_contributors: int
    avg_pr_merge_time: float | None
    open_issues: int
    raw_metrics: dict