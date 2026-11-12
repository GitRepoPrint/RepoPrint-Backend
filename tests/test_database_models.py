import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

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

pytestmark= pytest.mark.anyio


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def make_user(session, email="ana@example.com"):
    user= User(email=email, name="Ana")
    session.add(user)
    await session.flush()
    return user


async def make_repository(session, owner, url="https://github.com/o/r"):
    repository= Repository(user_id=owner.id, url=url, provider="github", name="o/r")
    session.add(repository)
    await session.flush()
    return repository


async def make_run(session, repository, user=None):
    run= AnalysisRun(repository_id=repository.id, user_id=user.id if user else None, status="completed")
    session.add(run)
    await session.flush()
    return run


async def test_rows_get_an_id_and_the_defaults(session):
    user= await make_user(session)
    repository= await make_repository(session, user)

    assert isinstance(user.id, uuid.UUID)
    assert isinstance(user.created_at, datetime)
    assert repository.is_private is False


async def test_email_is_unique(session):
    await make_user(session, "ana@example.com")

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await make_user(session, "ana@example.com")


async def test_a_run_has_at_most_one_metrics_row_and_one_dfd_graph(session):
    user= await make_user(session)
    run= await make_run(session, await make_repository(session, user), user)
    session.add_all([ActivityMetrics(analysis_run_id=run.id), DfdGraph(analysis_run_id=run.id, nodes=[], edges=[])])
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(ActivityMetrics(analysis_run_id=run.id))
            await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(DfdGraph(analysis_run_id=run.id, nodes=[], edges=[]))
            await session.flush()


async def test_a_user_joins_a_team_only_once(session):
    owner= await make_user(session)
    team= Team(name="Equipa", owner_id=owner.id)
    session.add(team)
    await session.flush()
    session.add(TeamMember(team_id=team.id, user_id=owner.id, role="manager"))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(TeamMember(team_id=team.id, user_id=owner.id))
            await session.flush()


async def test_the_same_provider_account_links_to_one_user(session):
    first= await make_user(session, "a@example.com")
    second= await make_user(session, "b@example.com")
    session.add(UserOAuth(user_id=first.id, provider="github", provider_user_id="42", access_token="x"))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(UserOAuth(user_id=second.id, provider="github", provider_user_id="42", access_token="y"))
            await session.flush()


async def test_json_columns_round_trip(session):
    user= await make_user(session)
    run= await make_run(session, await make_repository(session, user), user)
    nodes= [{"id": "n1", "kind": "process", "title": "API"}]
    edges= [{"source": "n1", "target": "n2", "label": "HTTPS"}]
    session.add_all([
        DfdGraph(analysis_run_id=run.id, nodes=nodes, edges=edges, languages=["python"]),
        ActivityMetrics(analysis_run_id=run.id, raw_metrics={"stars": 7, "open_prs": 3}),
    ])
    await session.flush()
    session.expire_all()

    graph= await session.scalar(select(DfdGraph))
    metrics= await session.scalar(select(ActivityMetrics))

    assert (graph.nodes, graph.edges, graph.languages)== (nodes, edges, ["python"])
    assert metrics.raw_metrics== {"stars": 7, "open_prs": 3}


async def build_account(session, email):
    user= await make_user(session, email)
    team= Team(name="Equipa", owner_id=user.id)
    session.add(team)
    await session.flush()
    repository= await make_repository(session, user, f"https://github.com/{email}/r")
    run= await make_run(session, repository, user)
    finding= ThreatFinding(analysis_run_id=run.id, category="Spoofing", threat_type="STRIDE")
    session.add_all([
        UserOAuth(user_id=user.id, provider="github", provider_user_id=email, access_token="x"),
        TeamMember(team_id=team.id, user_id=user.id),
        ActivityMetrics(analysis_run_id=run.id),
        DfdGraph(analysis_run_id=run.id, nodes=[], edges=[]),
        finding,
    ])
    await session.flush()
    conversation= ChatConversation(analysis_run_id=run.id, threat_finding_id=finding.id, user_id=user.id)
    session.add_all([
        FalsePositiveFeedback(threat_finding_id=finding.id, user_id=user.id, reason="teste"),
        conversation,
        AuditLog(user_id=user.id, repository_id=repository.id, action="analysis.run"),
    ])
    await session.flush()
    session.add(ChatMessage(conversation_id=conversation.id, sender_type="user", message="olá"))
    await session.flush()
    return user


async def test_deleting_an_account_removes_everything_that_belongs_to_it(session):
    leaving= await build_account(session, "sai@example.com")
    staying= await build_account(session, "fica@example.com")
    staying_email= staying.email

    await session.execute(delete(User).where(User.id== leaving.id))
    session.expire_all()

    for model in (UserOAuth, Team, TeamMember, Repository, AnalysisRun, ActivityMetrics, DfdGraph, ThreatFinding, FalsePositiveFeedback, ChatConversation, ChatMessage):
        assert await count(session, model)== 1, model.__name__
    assert await count(session, User)== 1
    assert (await session.scalar(select(User.email)))== staying_email


async def test_audit_logs_survive_an_account_deletion_without_the_user(session):
    user= await build_account(session, "sai@example.com")

    await session.execute(delete(User).where(User.id== user.id))
    session.expire_all()
    log= await session.scalar(select(AuditLog))

    assert log.action== "analysis.run"
    assert (log.user_id, log.repository_id)== (None, None)


async def test_deleting_a_repository_removes_its_analyses_and_keeps_the_audit_log(session):
    await build_account(session, "ana@example.com")

    await session.execute(delete(Repository))
    session.expire_all()

    for model in (AnalysisRun, ActivityMetrics, DfdGraph, ThreatFinding, FalsePositiveFeedback, ChatConversation, ChatMessage):
        assert await count(session, model)== 0, model.__name__
    assert await count(session, User)== 1
    assert await count(session, AuditLog)== 1
    assert (await session.scalar(select(AuditLog.repository_id))) is None


async def test_deleting_a_finding_keeps_the_conversation(session):
    await build_account(session, "ana@example.com")

    await session.execute(delete(ThreatFinding))
    session.expire_all()
    conversation= await session.scalar(select(ChatConversation))

    assert conversation.threat_finding_id is None
    assert await count(session, FalsePositiveFeedback)== 0
