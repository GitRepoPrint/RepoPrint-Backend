"""github tokens encrypted

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-11-16 10:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str= "d4e6f8a0b2c3"
down_revision: str | None= "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None= None
depends_on: str | Sequence[str] | None= None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(sa.table("users_oauth").delete())
    op.drop_column("users_oauth", "access_token")
    op.drop_column("users_oauth", "refresh_token")
    op.add_column("users_oauth", sa.Column("access_token", sa.LargeBinary(), nullable=False))
    op.add_column("users_oauth", sa.Column("refresh_token", sa.LargeBinary(), nullable=True))
    op.add_column("users_oauth", sa.Column("provider_username", sa.String(), nullable=True))
    op.create_unique_constraint(op.f("uq_users_oauth_user_id_provider"), "users_oauth", ["user_id", "provider"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_users_oauth_user_id_provider"), "users_oauth", type_="unique")
    op.drop_column("users_oauth", "provider_username")
    op.execute(sa.table("users_oauth").delete())
    op.drop_column("users_oauth", "refresh_token")
    op.drop_column("users_oauth", "access_token")
    op.add_column("users_oauth", sa.Column("access_token", sa.Text(), nullable=False))
    op.add_column("users_oauth", sa.Column("refresh_token", sa.Text(), nullable=True))
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
