from datetime import datetime
from pydantic import BaseModel

class ContributorsWindow(BaseModel):
    complete: bool
    commits_read: int
    since: datetime

class ActivityMetrics(BaseModel):
    owner: str
    repo: str
    default_branch: str
    total_commits: int
    active_contributors: int
    contributors_window: ContributorsWindow
    bots_excluded: int
    median_pr_merge_time: float | None
    open_issues: int
    fetched_at: datetime
    refresh_available_at: datetime
    cached: bool
    raw_metrics: dict