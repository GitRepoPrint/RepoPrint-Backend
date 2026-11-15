from datetime import datetime
from pydantic import BaseModel

class ContributorsWindow(BaseModel):
    complete: bool
    commits_read: int
    since: datetime

class LicenseInfo(BaseModel):
    spdx_id: str | None
    name: str | None

class BranchInfo(BaseModel):
    names: list[str]
    total: int
    protected: list[str]
    default_protected: bool

class CiInfo(BaseModel):
    provider: str
    conclusion: str | None
    passing: bool | None
    ran_at: datetime | None

class ContributorInfo(BaseModel):
    login: str | None
    commits: int
    last_commit_at: datetime

class CodeOwnersInfo(BaseModel):
    file: str | None
    coverage: float
    files_total: int
    files_owned: int

class RecentPr(BaseModel):
    number: int
    hours: float

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
    commit_sha: str | None= None
    license: LicenseInfo | None= None
    branches: BranchInfo | None= None
    ci: CiInfo | None= None
    weekly_commits: list[int] | None= None
    punch_card: list[list[int]] | None= None
    recent_prs: list[RecentPr]= []
    contributors: list[ContributorInfo]= []
    code_owners: CodeOwnersInfo | None= None