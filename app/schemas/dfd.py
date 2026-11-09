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


class DfdStats(BaseModel):
    files_parsed: int
    files_with_syntax_errors: int
    routes: int
    languages: dict[str, int]
    unsupported_languages: dict[str, str]


class DfdGraph(BaseModel):
    owner: str
    repo: str
    group_by: str
    nodes: list[DfdNode]
    edges: list[DfdEdge]
    stats: DfdStats
