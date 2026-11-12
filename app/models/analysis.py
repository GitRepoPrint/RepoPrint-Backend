import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin, foreign_key


class AnalysisRun(IdMixin, CreatedAtMixin, Base):
    __tablename__= "analysis_runs"

    repository_id: Mapped[uuid.UUID]= foreign_key("repositories.id")
    user_id: Mapped[uuid.UUID | None]= foreign_key("users.id", nullable=True, ondelete="SET NULL")
    status: Mapped[str]= mapped_column(String)
    commit_sha: Mapped[str | None]= mapped_column(String)
    branch: Mapped[str | None]= mapped_column(String)
    completed_at: Mapped[datetime | None]= mapped_column(DateTime(timezone=True))


class ActivityMetrics(IdMixin, Base):
    __tablename__= "activity_metrics"

    analysis_run_id: Mapped[uuid.UUID]= foreign_key("analysis_runs.id", unique=True)
    active_contributors: Mapped[int | None]= mapped_column(Integer)
    total_commits: Mapped[int | None]= mapped_column(Integer)
    median_pr_merge_time: Mapped[float | None]= mapped_column(Float)
    open_issues: Mapped[int | None]= mapped_column(Integer)
    raw_metrics: Mapped[dict | None]= mapped_column(JSONB)


class DfdGraph(IdMixin, CreatedAtMixin, Base):
    __tablename__= "dfd_graphs"

    analysis_run_id: Mapped[uuid.UUID]= foreign_key("analysis_runs.id", unique=True)
    nodes: Mapped[list]= mapped_column(JSONB)
    edges: Mapped[list]= mapped_column(JSONB)
    languages: Mapped[list | None]= mapped_column(JSONB)


class ThreatFinding(IdMixin, Base):
    __tablename__= "threat_findings"

    analysis_run_id: Mapped[uuid.UUID]= foreign_key("analysis_runs.id")
    category: Mapped[str]= mapped_column(String)
    threat_type: Mapped[str]= mapped_column(String)
    file_path: Mapped[str | None]= mapped_column(String)
    line: Mapped[int | None]= mapped_column(Integer)
    dfd_node_id: Mapped[str | None]= mapped_column(String)
    description: Mapped[str | None]= mapped_column(Text)
    recommendation_text: Mapped[str | None]= mapped_column(Text)
