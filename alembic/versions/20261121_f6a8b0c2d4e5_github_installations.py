"""github app installations

Revision ID: f6a8b0c2d4e5
Revises: e5f7a9b1c3d4
Create Date: 2026-11-21 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str= "f6a8b0c2d4e5"
down_revision: str | None= "e5f7a9b1c3d4"
branch_labels: str | Sequence[str] | None= None
depends_on: str | Sequence[str] | None= None


def upgrade() -> None:
    op.create_table("github_installations",
    sa.Column("user_id", sa.UUID(), nullable=False),
    sa.Column("installation_id", sa.BigInteger(), nullable=False),
    sa.Column("account_login", sa.String(), nullable=False),
    sa.Column("account_type", sa.String(), nullable=False),
    sa.Column("repository_selection", sa.String(), nullable=False),
    sa.Column("failed_since", sa.DateTime(timezone=True), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_github_installations_user_id_users"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_github_installations")),
    sa.UniqueConstraint("user_id", "installation_id", name=op.f("uq_github_installations_user_id_installation_id"))
    )
    op.create_index(op.f("ix_github_installations_user_id"), "github_installations", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_github_installations_user_id"), table_name="github_installations")
    op.drop_table("github_installations")
