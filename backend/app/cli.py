"""CLI de operación: ``python -m app.cli <comando>``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.persistence import db


async def _create_admin(username: str) -> int:
    from app.auth.service import create_user

    password = getpass.getpass("Contraseña (mín. 12 caracteres): ")
    if password != getpass.getpass("Repite la contraseña: "):
        print("Las contraseñas no coinciden", file=sys.stderr)
        return 1
    settings = get_settings()
    db.init_engine(settings.database_url.get_secret_value())
    try:
        async with db.session_scope() as s:
            await create_user(s, username, password, role="admin")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await db.dispose_engine()
    print(f"Usuario administrador '{username}' creado")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bottrade")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_admin = sub.add_parser("create-admin", help="Crea un usuario administrador")
    p_admin.add_argument("username")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    if args.cmd == "create-admin":
        return asyncio.run(_create_admin(args.username))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
