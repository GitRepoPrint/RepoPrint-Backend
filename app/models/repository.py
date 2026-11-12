import uuid

from sqlalchemy import Boolean, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin, foreign_key


class Repository(IdMixin, CreatedAtMixin, Base):
    __tablename__= "repositories"

    user_id: Mapped[uuid.UUID | None]= foreign_key("users.id", nullable=True)
    team_id: Mapped[uuid.UUID | None]= foreign_key("teams.id", nullable=True)
    url: Mapped[str]= mapped_column(String)
    provider: Mapped[str]= mapped_column(String)
    name: Mapped[str]= mapped_column(String)
    default_branch: Mapped[str | None]= mapped_column(String)
    is_private: Mapped[bool]= mapped_column(Boolean, server_default=false())
