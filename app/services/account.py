from sqlalchemy import delete, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ActivityMetrics,
    AnalysisRun,
    AuditLog,
    ChatConversation,
    ChatMessage,
    DfdGraph,
    FalsePositiveFeedback,
    Repository,
    Team,
    TeamMember,
    ThreatFinding,
    User,
    UserOAuth,
)
from app.services import audit, keycloak_admin

SECRET_COLUMNS= {"access_token", "refresh_token"}


def row_dict(row) -> dict:
    columns= inspect(type(row)).mapper.column_attrs
    return {column.key: getattr(row, column.key) for column in columns if column.key not in SECRET_COLUMNS}


async def rows(session: AsyncSession, model, condition) -> list[dict]:
    return [row_dict(row) for row in await session.scalars(select(model).where(condition))]


async def export_user_data(session: AsyncSession, user: User) -> dict:
    repositories= await rows(session, Repository, Repository.user_id == user.id)
    repository_ids= [repository["id"] for repository in repositories]
    runs= await rows(session, AnalysisRun, (AnalysisRun.user_id == user.id) | AnalysisRun.repository_id.in_(repository_ids))
    run_ids= [run["id"] for run in runs]
    conversations= await rows(session, ChatConversation, ChatConversation.user_id == user.id)
    conversation_ids= [conversation["id"] for conversation in conversations]
    return {
        "account": row_dict(user),
        "linked_accounts": await rows(session, UserOAuth, UserOAuth.user_id == user.id),
        "teams_owned": await rows(session, Team, Team.owner_id == user.id),
        "team_memberships": await rows(session, TeamMember, TeamMember.user_id == user.id),
        "repositories": repositories,
        "analysis_runs": runs,
        "activity_metrics": await rows(session, ActivityMetrics, ActivityMetrics.analysis_run_id.in_(run_ids)),
        "dfd_graphs": await rows(session, DfdGraph, DfdGraph.analysis_run_id.in_(run_ids)),
        "threat_findings": await rows(session, ThreatFinding, ThreatFinding.analysis_run_id.in_(run_ids)),
        "false_positive_feedbacks": await rows(session, FalsePositiveFeedback, FalsePositiveFeedback.user_id == user.id),
        "chat_conversations": conversations,
        "chat_messages": await rows(session, ChatMessage, ChatMessage.conversation_id.in_(conversation_ids)),
        "audit_logs": await rows(session, AuditLog, AuditLog.user_id == user.id),
    }


async def delete_account(session: AsyncSession, sub: str) -> None:
    removed= await session.execute(delete(User).where(User.keycloak_sub == sub).returning(User.id))
    audit.record(session, "account.deleted", details={"had_account": removed.first() is not None})
    await session.commit()
    await keycloak_admin.delete_user(sub)
