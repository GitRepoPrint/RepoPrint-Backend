import uuid

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin, foreign_key


class User(IdMixin, CreatedAtMixin, Base):
    __tablename__= "users"

    keycloak_sub: Mapped[str | None]= mapped_column(String, unique=True)
    email: Mapped[str]= mapped_column(String, unique=True)
    name: Mapped[str | None]= mapped_column(String)
    role: Mapped[str | None]= mapped_column(String)


class UserOAuth(IdMixin, CreatedAtMixin, Base):
    __tablename__= "users_oauth"
    __table_args__= (UniqueConstraint("provider", "provider_user_id"),)

    user_id: Mapped[uuid.UUID]= foreign_key("users.id")
    provider: Mapped[str]= mapped_column(String)
    provider_user_id: Mapped[str]= mapped_column(String)
    access_token: Mapped[str]= mapped_column(Text)
    refresh_token: Mapped[str | None]= mapped_column(Text)


class Team(IdMixin, CreatedAtMixin, Base):
    __tablename__= "teams"

    name: Mapped[str]= mapped_column(String)
    owner_id: Mapped[uuid.UUID]= foreign_key("users.id")


class TeamMember(IdMixin, Base):
    __tablename__= "team_members"
    __table_args__= (UniqueConstraint("team_id", "user_id"),)

    team_id: Mapped[uuid.UUID]= foreign_key("teams.id")
    user_id: Mapped[uuid.UUID]= foreign_key("users.id")
    role: Mapped[str | None]= mapped_column(String)
