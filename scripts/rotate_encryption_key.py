import asyncio
import os
import sys

from app.db.session import get_engine, get_sessionmaker
from app.services.key_rotation import KeyRotationError, rotate_encryption_key


async def main(dry_run: bool) -> int:
    old_key= os.environ.get("OLD_TOKEN_ENCRYPTION_KEY", "")
    new_key= os.environ.get("NEW_TOKEN_ENCRYPTION_KEY", "")
    if not old_key or not new_key:
        print("Define OLD_TOKEN_ENCRYPTION_KEY e NEW_TOKEN_ENCRYPTION_KEY no ambiente")
        return 2
    try:
        async with get_sessionmaker()() as session:
            count= await rotate_encryption_key(session, old_key, new_key, dry_run=dry_run)
    except KeyRotationError as error:
        print(error)
        return 1
    finally:
        await get_engine().dispose()
    print(f"{count} ligações verificadas, nada alterado" if dry_run else f"{count} ligações recifradas com a chave nova")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv)))
