import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepositoryIn(BaseModel):
    url: str


class RepositoryOut(BaseModel):
    model_config= ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    provider: str
    is_private: bool
    default_branch: str | None
    created_at: datetime
