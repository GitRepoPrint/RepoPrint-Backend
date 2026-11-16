"""keycloak schema

Revision ID: 4b7a9c1d2e30
Revises: 9c54111bf738
Create Date: 2026-11-14 17:40:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str= "4b7a9c1d2e30"
down_revision: str | None= "9c54111bf738"
branch_labels: str | Sequence[str] | None= None
depends_on: str | Sequence[str] | None= None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("keycloak", if_not_exists=True))


def downgrade() -> None:
    pass
