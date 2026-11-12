"""initial schema

Revision ID: 9c54111bf738
Revises: 
Create Date: 2026-11-12 15:48:36.956448
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str= "9c54111bf738"
down_revision: str | None= None
branch_labels: str | Sequence[str] | None= None
depends_on: str | Sequence[str] | None= None


def upgrade() -> None:
    op.create_table("users",
    sa.Column("email", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=True),
    sa.Column("role", sa.String(), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    sa.UniqueConstraint("email", name=op.f("uq_users_email"))
    )
    op.create_table("teams",
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("owner_id", sa.UUID(), nullable=False),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_teams_owner_id_users"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_teams"))
    )
    op.create_index(op.f("ix_teams_owner_id"), "teams", ["owner_id"], unique=False)
    op.create_table("users_oauth",
    sa.Column("user_id", sa.UUID(), nullable=False),
    sa.Column("provider", sa.String(), nullable=False),
    sa.Column("provider_user_id", sa.String(), nullable=False),
    sa.Column("access_token", sa.Text(), nullable=False),
    sa.Column("refresh_token", sa.Text(), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_users_oauth_user_id_users"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_users_oauth")),
    sa.UniqueConstraint("provider", "provider_user_id", name=op.f("uq_users_oauth_provider_provider_user_id"))
    )
    op.create_index(op.f("ix_users_oauth_user_id"), "users_oauth", ["user_id"], unique=False)
    op.create_table("repositories",
    sa.Column("user_id", sa.UUID(), nullable=True),
    sa.Column("team_id", sa.UUID(), nullable=True),
    sa.Column("url", sa.String(), nullable=False),
    sa.Column("provider", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("default_branch", sa.String(), nullable=True),
    sa.Column("is_private", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_repositories_team_id_teams"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_repositories_user_id_users"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories"))
    )
    op.create_index(op.f("ix_repositories_team_id"), "repositories", ["team_id"], unique=False)
    op.create_index(op.f("ix_repositories_user_id"), "repositories", ["user_id"], unique=False)
    op.create_table("team_members",
    sa.Column("team_id", sa.UUID(), nullable=False),
    sa.Column("user_id", sa.UUID(), nullable=False),
    sa.Column("role", sa.String(), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_team_members_team_id_teams"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_team_members_user_id_users"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_team_members")),
    sa.UniqueConstraint("team_id", "user_id", name=op.f("uq_team_members_team_id_user_id"))
    )
    op.create_index(op.f("ix_team_members_team_id"), "team_members", ["team_id"], unique=False)
    op.create_index(op.f("ix_team_members_user_id"), "team_members", ["user_id"], unique=False)
    op.create_table("analysis_runs",
    sa.Column("repository_id", sa.UUID(), nullable=False),
    sa.Column("user_id", sa.UUID(), nullable=True),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("commit_sha", sa.String(), nullable=True),
    sa.Column("branch", sa.String(), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], name=op.f("fk_analysis_runs_repository_id_repositories"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_analysis_runs_user_id_users"), ondelete="SET NULL"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs"))
    )
    op.create_index(op.f("ix_analysis_runs_repository_id"), "analysis_runs", ["repository_id"], unique=False)
    op.create_index(op.f("ix_analysis_runs_user_id"), "analysis_runs", ["user_id"], unique=False)
    op.create_table("audit_logs",
    sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("user_id", sa.UUID(), nullable=True),
    sa.Column("repository_id", sa.UUID(), nullable=True),
    sa.Column("action", sa.String(), nullable=False),
    sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], name=op.f("fk_audit_logs_repository_id_repositories"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_audit_logs_user_id_users"), ondelete="SET NULL"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs"))
    )
    op.create_index(op.f("ix_audit_logs_repository_id"), "audit_logs", ["repository_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_table("activity_metrics",
    sa.Column("analysis_run_id", sa.UUID(), nullable=False),
    sa.Column("active_contributors", sa.Integer(), nullable=True),
    sa.Column("total_commits", sa.Integer(), nullable=True),
    sa.Column("median_pr_merge_time", sa.Float(), nullable=True),
    sa.Column("open_issues", sa.Integer(), nullable=True),
    sa.Column("raw_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], name=op.f("fk_activity_metrics_analysis_run_id_analysis_runs"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_metrics")),
    sa.UniqueConstraint("analysis_run_id", name=op.f("uq_activity_metrics_analysis_run_id"))
    )
    op.create_table("dfd_graphs",
    sa.Column("analysis_run_id", sa.UUID(), nullable=False),
    sa.Column("nodes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("edges", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("languages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], name=op.f("fk_dfd_graphs_analysis_run_id_analysis_runs"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_dfd_graphs")),
    sa.UniqueConstraint("analysis_run_id", name=op.f("uq_dfd_graphs_analysis_run_id"))
    )
    op.create_table("threat_findings",
    sa.Column("analysis_run_id", sa.UUID(), nullable=False),
    sa.Column("category", sa.String(), nullable=False),
    sa.Column("threat_type", sa.String(), nullable=False),
    sa.Column("file_path", sa.String(), nullable=True),
    sa.Column("line", sa.Integer(), nullable=True),
    sa.Column("dfd_node_id", sa.String(), nullable=True),
    sa.Column("description", sa.Text(), nullable=True),
    sa.Column("recommendation_text", sa.Text(), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], name=op.f("fk_threat_findings_analysis_run_id_analysis_runs"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_threat_findings"))
    )
    op.create_index(op.f("ix_threat_findings_analysis_run_id"), "threat_findings", ["analysis_run_id"], unique=False)
    op.create_table("chat_conversations",
    sa.Column("analysis_run_id", sa.UUID(), nullable=False),
    sa.Column("threat_finding_id", sa.UUID(), nullable=True),
    sa.Column("user_id", sa.UUID(), nullable=False),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], name=op.f("fk_chat_conversations_analysis_run_id_analysis_runs"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["threat_finding_id"], ["threat_findings.id"], name=op.f("fk_chat_conversations_threat_finding_id_threat_findings"), ondelete="SET NULL"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_chat_conversations_user_id_users"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_conversations"))
    )
    op.create_index(op.f("ix_chat_conversations_analysis_run_id"), "chat_conversations", ["analysis_run_id"], unique=False)
    op.create_index(op.f("ix_chat_conversations_threat_finding_id"), "chat_conversations", ["threat_finding_id"], unique=False)
    op.create_index(op.f("ix_chat_conversations_user_id"), "chat_conversations", ["user_id"], unique=False)
    op.create_table("false_positive_feedbacks",
    sa.Column("threat_finding_id", sa.UUID(), nullable=False),
    sa.Column("user_id", sa.UUID(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=True),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["threat_finding_id"], ["threat_findings.id"], name=op.f("fk_false_positive_feedbacks_threat_finding_id_threat_findings"), ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_false_positive_feedbacks_user_id_users"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_false_positive_feedbacks"))
    )
    op.create_index(op.f("ix_false_positive_feedbacks_threat_finding_id"), "false_positive_feedbacks", ["threat_finding_id"], unique=False)
    op.create_index(op.f("ix_false_positive_feedbacks_user_id"), "false_positive_feedbacks", ["user_id"], unique=False)
    op.create_table("chat_messages",
    sa.Column("conversation_id", sa.UUID(), nullable=False),
    sa.Column("sender_type", sa.String(), nullable=False),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], name=op.f("fk_chat_messages_conversation_id_chat_conversations"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages"))
    )
    op.create_index(op.f("ix_chat_messages_conversation_id"), "chat_messages", ["conversation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_messages_conversation_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_false_positive_feedbacks_user_id"), table_name="false_positive_feedbacks")
    op.drop_index(op.f("ix_false_positive_feedbacks_threat_finding_id"), table_name="false_positive_feedbacks")
    op.drop_table("false_positive_feedbacks")
    op.drop_index(op.f("ix_chat_conversations_user_id"), table_name="chat_conversations")
    op.drop_index(op.f("ix_chat_conversations_threat_finding_id"), table_name="chat_conversations")
    op.drop_index(op.f("ix_chat_conversations_analysis_run_id"), table_name="chat_conversations")
    op.drop_table("chat_conversations")
    op.drop_index(op.f("ix_threat_findings_analysis_run_id"), table_name="threat_findings")
    op.drop_table("threat_findings")
    op.drop_table("dfd_graphs")
    op.drop_table("activity_metrics")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_repository_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_analysis_runs_user_id"), table_name="analysis_runs")
    op.drop_index(op.f("ix_analysis_runs_repository_id"), table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index(op.f("ix_team_members_user_id"), table_name="team_members")
    op.drop_index(op.f("ix_team_members_team_id"), table_name="team_members")
    op.drop_table("team_members")
    op.drop_index(op.f("ix_repositories_user_id"), table_name="repositories")
    op.drop_index(op.f("ix_repositories_team_id"), table_name="repositories")
    op.drop_table("repositories")
    op.drop_index(op.f("ix_users_oauth_user_id"), table_name="users_oauth")
    op.drop_table("users_oauth")
    op.drop_index(op.f("ix_teams_owner_id"), table_name="teams")
    op.drop_table("teams")
    op.drop_table("users")
