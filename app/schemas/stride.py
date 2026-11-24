"""Modelos Pydantic para o motor de análise de ameaças STRIDE."""
from typing import Literal
from pydantic import BaseModel, Field

StrideSeverity = Literal["Critical", "High", "Medium", "Low"]
StrideCategory = Literal[
    "Spoofing",
    "Tampering",
    "Repudiation",
    "Information Disclosure",
    "Denial of Service",
    "Elevation of Privilege",
]
TargetType = Literal["process", "data_store", "data_flow", "external_entity"]
FindingType = Literal["vulnerability", "architectural_hardening"]
ConfidenceLevel = Literal["High", "Medium", "Low"]


class StrideFinding(BaseModel):
    threat_id: str
    category: StrideCategory | str
    stride_letter: str = ""
    severity: StrideSeverity | str
    target_type: TargetType | str
    finding_type: FindingType | str = "vulnerability"
    confidence: ConfidenceLevel | str = "High"
    component_id: str | None = None
    component_name: str | None = None
    title: str
    description: str
    mitigation: str = ""
    references: list[str] = Field(default_factory=list)
    source: str | None = None
    target: str | None = None
    protocol: str | None = None
    file_path: str | None = None


class StrideReport(BaseModel):
    repository: str
    total_findings: int
    actionable_vulnerabilities_count: int
    architectural_recommendations_count: int
    summary_by_category: dict[str, int] = Field(default_factory=dict)
    summary_by_severity: dict[str, int] = Field(default_factory=dict)
    summary_by_type: dict[str, int] = Field(default_factory=dict)
    summary_by_confidence: dict[str, int] = Field(default_factory=dict)
    stride_matrix: dict[str, list[str]] = Field(default_factory=dict)
    findings: list[StrideFinding] = Field(default_factory=list)
    actionable_vulnerabilities: list[StrideFinding] = Field(default_factory=list)
    architectural_recommendations: list[StrideFinding] = Field(default_factory=list)
