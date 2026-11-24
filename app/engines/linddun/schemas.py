"""Modelos Pydantic para o motor de privacidade LINDDUN GO puro."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

LinddunCategory = Literal[
    "Linking",
    "Identifying",
    "Non-repudiation",
    "Detecting",
    "Data disclosure",
    "Unawareness and unintervenability",
    "Non-compliance",
]

TargetType = Literal["process", "data_store", "data_flow", "external_entity", "actor"]


class DictLikeModel(BaseModel):
    """Modelo base que permite acesso estilo dicionário para compatibilidade retroativa."""

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class LinddunThreat(DictLikeModel):
    """Ameaça identificada correspondente a uma carta do baralho LINDDUN GO."""

    threat_id: str
    category: str
    title: str
    hotspot: str
    threat_sources: list[str] = Field(default_factory=list)
    target_type: str
    component_id: str
    component_name: str | None = None
    description: str
    elicitation_questions: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    additional_info: str = ""
    source: str | None = None
    target: str | None = None
    file_path: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# Alias para retrocompatibilidade
LinddunFinding = LinddunThreat


class LinddunSummary(DictLikeModel):
    """Sumário quantitativo das ameaças LINDDUN GO por categoria e fonte."""

    total_threats: int
    by_category: dict[str, int] = Field(default_factory=dict)
    by_threat_source: dict[str, int] = Field(default_factory=dict)
    by_target_type: dict[str, int] = Field(default_factory=dict)

    # Compatibilidade com UI existente
    total_findings: int | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.total_findings is None:
            self.total_findings = self.total_threats


class LinddunReport(DictLikeModel):
    """Relatório final de análise de ameaças LINDDUN GO."""

    repository: str
    analyzed_at: datetime
    summary: LinddunSummary
    threats: list[LinddunThreat]
    findings: list[LinddunThreat] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.findings:
            self.findings = self.threats
