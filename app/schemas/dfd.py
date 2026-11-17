"""Modelos da resposta de GET /dfd. Seguem docs/dfd-schema.md (schema v1, nível "detail")."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Kind= Literal["external_entity", "process", "data_store"]
Source= Literal["config", "code", "manual"]
Confidence= Literal["high", "medium", "low"]


class Evidence(BaseModel):
    file: str | None= None
    line: int | None= None
    span: int | None= None
    note: str | None= None


class Provenance(BaseModel):
    source: Source
    confidence: Confidence
    evidence: list[Evidence]= Field(min_length=1)


class DfdNode(BaseModel):
    id: str
    kind: Kind
    name: str
    technology: str | None= None
    tags: list[str]= Field(default_factory=list)
    parent: str | None= None
    path: str | None= None
    provenance: Provenance
    extra: dict= Field(default_factory=dict)


class DfdEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    operations: list[str]= Field(default_factory=list)
    protocol: str | None= None
    provenance: Provenance
    extra: dict= Field(default_factory=dict)


class DfdFlowResource(BaseModel):
    id: str
    kind: str
    label: str
    operations: list[str]
    path: list[str]
    site: dict


class DfdFlow(BaseModel):
    id: str
    route: str
    method: str
    path: str
    handler: str
    file: str
    process: str
    resources: list[DfdFlowResource]


class DfdStats(BaseModel):
    files_parsed: int
    files_with_syntax_errors: int
    routes: int
    flows: int
    languages: dict[str, int]
    unsupported_languages: dict[str, str]


class RepositoryRef(BaseModel):
    owner: str
    repo: str
    ref: str | None= None


class GraphMeta(BaseModel):
    producer: Literal["browser-overview", "backend-detail"]
    generated_at: datetime
    group_by: str | None= None
    stats: DfdStats | None= None


class GraphExtra(BaseModel):
    flows: list[DfdFlow]= Field(default_factory=list)


class DfdGraph(BaseModel):
    schema_version: Literal["1"]
    repository: RepositoryRef
    level: Literal["overview", "detail"]
    nodes: list[DfdNode]
    edges: list[DfdEdge]
    meta: GraphMeta
    extra: GraphExtra= Field(default_factory=GraphExtra)
