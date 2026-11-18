"""repositories unique per user and url

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-11-17 10:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str= "e5f7a9b1c3d4"
down_revision: str | None= "d4e6f8a0b2c3"
branch_labels: str | Sequence[str] | None= None
depends_on: str | Sequence[str] | None= None


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_repositories_user_id_url"), "repositories", ["user_id", "url"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_repositories_user_id_url"), "repositories", type_="unique")
