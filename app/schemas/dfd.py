from pydantic import BaseModel


class Position(BaseModel):
    x: float
    y: float


class DfdNode(BaseModel):
    id: str
    type: str
    data: dict
    position: Position


class DfdEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    data: dict


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


class DfdGraph(BaseModel):
    owner: str
    repo: str
    group_by: str
    nodes: list[DfdNode]
    edges: list[DfdEdge]
    flows: list[DfdFlow]
    stats: DfdStats
