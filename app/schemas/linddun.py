"""Modelos Pydantic para o motor de privacidade LINDDUN."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

LinddunSeverity = Literal["Critical", "High", "Medium", "Low", "Info"]

LinddunCategory = Literal[
    "Linkability",
    "Identifiability",
    "Non-repudiation",
    "Detectability",
    "Disclosure of information",
    "Unawareness",
    "Non-compliance",
]

TargetType = Literal["process", "data_store", "data_flow", "external_entity", "actor"]


class LinddunFinding(BaseModel):
    threat_id: str
    category: str
    severity: str
    target_type: str
    component_id: str
    component_name: str | None = None
    title: str
    description: str
    gdpr_articles: list[str] = Field(default_factory=list)
    mitigation: str = ""
    source: str | None = None
    target: str | None = None
    file_path: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class LinddunSummary(BaseModel):
    total_findings: int
    by_category: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)


class LinddunReport(BaseModel):
    repository: str
    analyzed_at: datetime
    summary: LinddunSummary
    findings: list[LinddunFinding]
