import uuid

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config= ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str | None
    role: str | None
