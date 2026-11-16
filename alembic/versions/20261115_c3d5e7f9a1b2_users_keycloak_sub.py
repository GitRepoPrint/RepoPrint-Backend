"""users keycloak_sub

Revision ID: c3d5e7f9a1b2
Revises: 4b7a9c1d2e30
Create Date: 2026-11-15 10:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str= "c3d5e7f9a1b2"
down_revision: str | None= "4b7a9c1d2e30"
branch_labels: str | Sequence[str] | None= None
depends_on: str | Sequence[str] | None= None


def upgrade() -> None:
    op.add_column("users", sa.Column("keycloak_sub", sa.String(), nullable=True))
    op.create_unique_constraint(op.f("uq_users_keycloak_sub"), "users", ["keycloak_sub"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_users_keycloak_sub"), "users", type_="unique")
    op.drop_column("users", "keycloak_sub")
