import pytest
from pydantic import SecretStr
from sqlalchemy import Text, func, literal, select

from app.config import settings
from app.models import User, UserOAuth
from app.services import github_oauth
from app.services.key_rotation import KeyRotationError, rotate_encryption_key

pytestmark= pytest.mark.anyio

OLD= "o" * 32
NEW= "n" * 40


async def add_connection(session, name, token, refresh=None):
    user= User(email=f"{name}@example.com", name=name, keycloak_sub=f"sub-{name}")
    session.add(user)
    await session.flush()
    row= UserOAuth(
        user_id=user.id,
        provider="github",
        provider_user_id=name,
        provider_username=name,
        access_token=func.pgp_sym_encrypt(literal(token, Text), literal(OLD, Text)),
        refresh_token=func.pgp_sym_encrypt(literal(refresh, Text), literal(OLD, Text)) if refresh else None,
    )
    session.add(row)
    await session.flush()
    return user


async def token_with(session, user, key):
    return await session.scalar(
        select(func.pgp_sym_decrypt(UserOAuth.access_token, literal(key, Text))).where(UserOAuth.user_id == user.id)
    )


async def test_rotation_moves_every_token_to_the_new_key(session, monkeypatch):
    ana= await add_connection(session, "ana", "gho_ana", refresh="ghr_ana")
    bia= await add_connection(session, "bia", "gho_bia")

    assert await rotate_encryption_key(session, OLD, NEW)== 2

    assert await token_with(session, ana, NEW)== "gho_ana"
    assert await token_with(session, bia, NEW)== "gho_bia"
    refresh= await session.scalar(
        select(func.pgp_sym_decrypt(UserOAuth.refresh_token, literal(NEW, Text))).where(UserOAuth.user_id == ana.id)
    )
    assert refresh== "ghr_ana"
    other= await session.scalar(select(UserOAuth.refresh_token).where(UserOAuth.user_id == bia.id))
    assert other is None
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(NEW))
    assert await github_oauth.user_token(session, ana.id)== "gho_ana"


async def test_rotated_tokens_use_aes_256(session):
    ana= await add_connection(session, "ana", "gho_ana")

    await rotate_encryption_key(session, OLD, NEW)

    packet= bytes(await session.scalar(select(UserOAuth.access_token).where(UserOAuth.user_id == ana.id)))
    assert packet[3]== 9


async def test_the_old_key_stops_working(session):
    ana= await add_connection(session, "ana", "gho_ana")
    await rotate_encryption_key(session, OLD, NEW)

    with pytest.raises(Exception):
        await token_with(session, ana, OLD)


async def test_a_wrong_old_key_changes_nothing(session):
    ana= await add_connection(session, "ana", "gho_ana")

    with pytest.raises(KeyRotationError):
        await rotate_encryption_key(session, "x" * 32, NEW)

    assert await token_with(session, ana, OLD)== "gho_ana"


async def test_dry_run_only_checks(session):
    ana= await add_connection(session, "ana", "gho_ana")

    assert await rotate_encryption_key(session, OLD, NEW, dry_run=True)== 1

    assert await token_with(session, ana, OLD)== "gho_ana"


@pytest.mark.parametrize("new_key", ["short", OLD])
async def test_a_weak_or_unchanged_key_is_refused(session, new_key):
    await add_connection(session, "ana", "gho_ana")

    with pytest.raises(KeyRotationError):
        await rotate_encryption_key(session, OLD, new_key)


async def test_without_connections_it_does_nothing(session):
    assert await rotate_encryption_key(session, OLD, NEW)== 0
