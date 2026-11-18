from sqlalchemy import Text, func, literal, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserOAuth
from app.services.github_oauth import CIPHER_OPTIONS

MIN_KEY_LENGTH= 32


class KeyRotationError(Exception):
    pass


def _text(value: str):
    return literal(value, Text)


async def rotate_encryption_key(session: AsyncSession, old_key: str, new_key: str, *, dry_run: bool= False) -> int:
    if len(new_key) < MIN_KEY_LENGTH:
        raise KeyRotationError(f"A chave nova precisa de pelo menos {MIN_KEY_LENGTH} caracteres")
    if new_key == old_key:
        raise KeyRotationError("A chave nova é igual à antiga")

    total= await session.scalar(select(func.count()).select_from(UserOAuth))
    try:
        async with session.begin_nested():
            await session.execute(select(func.pgp_sym_decrypt(UserOAuth.access_token, _text(old_key))))
            await session.execute(
                select(func.pgp_sym_decrypt(UserOAuth.refresh_token, _text(old_key))).where(UserOAuth.refresh_token.is_not(None))
            )
    except DBAPIError:
        raise KeyRotationError("Há tokens que a chave antiga não decifra; nada foi alterado")

    if dry_run or not total:
        return total or 0

    options= _text(CIPHER_OPTIONS)
    await session.execute(
        update(UserOAuth).values(
            access_token=func.pgp_sym_encrypt(func.pgp_sym_decrypt(UserOAuth.access_token, _text(old_key)), _text(new_key), options),
            refresh_token=func.pgp_sym_encrypt(func.pgp_sym_decrypt(UserOAuth.refresh_token, _text(old_key)), _text(new_key), options),
        )
    )
    await session.commit()
    return total
