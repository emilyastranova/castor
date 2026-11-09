"""castor-admin: account recovery from the server's shell, for when nobody can sign in.

    castor-admin list-users
    castor-admin reset-password <username> [--password ...]   (prompts, or generates one)
    castor-admin enable <username>
    castor-admin promote <username>
    castor-admin create-admin <username> [--password ...]

In the compose stack: `docker compose exec server castor-admin <command>`. Uses the same
settings (CASTOR_DATABASE_URL, ...) as the server, and brings the database up to date first.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys

from sqlalchemy import func, select

from . import models as m
from .db import SessionLocal, run_migrations
from .security import hash_password


def _password(given: str | None) -> tuple[str, bool]:
    """(password, generated?)."""
    if given:
        return given, False
    if sys.stdin.isatty():
        first = getpass.getpass("New password (empty: generate one): ")
        if first:
            if getpass.getpass("Again: ") != first:
                sys.exit("The passwords don't match")
            return first, False
    return secrets.token_urlsafe(12), True


async def _user(db, username: str) -> m.User:
    user = (await db.execute(select(m.User).where(func.lower(m.User.username) == username.lower()))).scalar_one_or_none()
    if user is None:
        sys.exit(f"No user named {username} (see: castor-admin list-users)")
    return user


async def run(args: argparse.Namespace) -> None:
    await run_migrations()
    async with SessionLocal() as db:
        if args.command == "list-users":
            rows = (await db.execute(select(m.User).order_by(m.User.username))).scalars().all()
            for u in rows:
                state = "disabled" if u.disabled_at else "active"
                print(f"{u.username:<24} {u.role.value:<9} {state:<9} {u.display_name}")
            if not rows:
                print("No users yet; the server creates the first admin when it starts.")
            return
        if args.command == "create-admin":
            taken = await db.execute(select(m.User.id).where(func.lower(m.User.username) == args.username.lower()))
            if taken.first() is not None:
                sys.exit(f"{args.username} exists; use reset-password / promote / enable instead")
            password, generated = _password(args.password)
            if len(password) < 8:
                sys.exit("Use at least 8 characters")
            db.add(m.User(username=args.username, display_name=args.username, role=m.UserRole.admin,
                          password_hash=hash_password(password)))
            await db.commit()
            print(f"Created admin {args.username}" + (f" with password: {password}" if generated else ""))
            return
        if args.command == "password-login":
            from .app_settings import save

            await save(db, {"password_login": args.state == "on"}, None)
            await db.commit()
            print("Password sign-in is " + ("on" if args.state == "on" else "off (single sign-on only, while a provider is enabled)"))
            return
        user = await _user(db, args.username)
        if args.command == "reset-password":
            password, generated = _password(args.password)
            if len(password) < 8:
                sys.exit("Use at least 8 characters")
            user.password_hash = hash_password(password)
            user.disabled_at = None
            await db.commit()
            print(f"Reset the password of {user.username} (and enabled the account)"
                  + (f": {password}" if generated else ""))
        elif args.command == "enable":
            user.disabled_at = None
            await db.commit()
            print(f"Enabled {user.username}")
        elif args.command == "promote":
            user.role = m.UserRole.admin
            user.disabled_at = None
            await db.commit()
            print(f"{user.username} is now an admin")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="castor-admin", description="Castor account recovery")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list-users", help="list users, their roles and whether they're enabled")
    for name, text in (("reset-password", "set a new password (and enable the account)"),
                       ("create-admin", "add a new admin")):
        c = sub.add_parser(name, help=text)
        c.add_argument("username")
        c.add_argument("--password", help="the new password (default: prompt, or generate one)")
    for name, text in (("enable", "re-enable a disabled account"), ("promote", "make a user an admin (and enable them)")):
        sub.add_parser(name, help=text).add_argument("username")
    sub.add_parser("password-login", help="turn signing in with a password on or off (e.g. when single sign-on is broken)") \
        .add_argument("state", choices=["on", "off"])
    asyncio.run(run(p.parse_args(argv)))


if __name__ == "__main__":
    main()
