"""Nobody gets locked out: the last admin stays, startup restores an admin, and
castor-admin recovers accounts from the server's shell."""

import asyncio
import tempfile
import uuid
from pathlib import Path

from alembic import command

from castor_server.db import alembic_config


def _fresh_db() -> str:
    url = f"sqlite+aiosqlite:///{Path(tempfile.mkdtemp()) / 'castor.db'}"
    command.upgrade(alembic_config(url), "head")
    return url


def test_startup_restores_an_admin_when_there_is_none():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from castor_server import models as m
    from castor_server.accounts import ensure_active_admin

    url = _fresh_db()

    async def scenario():
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            assert await ensure_active_admin(db) is None  # no users: the first admin is made elsewhere
            from datetime import UTC, datetime, timedelta

            t0 = datetime(2026, 1, 1, tzinfo=UTC)
            db.add_all([
                m.User(username="old", display_name="old", password_hash="x", role=m.UserRole.operator,
                       disabled_at=t0, created_at=t0),
                m.User(username="bob", display_name="bob", password_hash="x", role=m.UserRole.operator,
                       created_at=t0 + timedelta(days=1)),
                m.User(username="eve", display_name="eve", password_hash="x", role=m.UserRole.viewer,
                       created_at=t0 + timedelta(days=2)),
            ])
            await db.commit()
            # The longest-standing *active* user is promoted.
            assert (await ensure_active_admin(db)).username == "bob"
            assert await ensure_active_admin(db) is None  # now there is one

            # Every account disabled: the longest-standing one comes back as admin.
            for u in (await db.execute(m.User.__table__.select())).all():
                await db.execute(m.User.__table__.update().where(m.User.id == u.id).values(disabled_at=t0))
            await db.commit()
            restored = await ensure_active_admin(db)
            assert restored.username == "old" and restored.disabled_at is None and restored.is_admin
        await engine.dispose()

    asyncio.run(scenario())


def test_sole_admin_cannot_demote_or_disable_themselves(client, auth):
    me = client.get("/api/auth/me", headers=auth).json()
    others = [u for u in client.get("/api/users", headers=auth).json()
              if u["role"] == "admin" and not u["disabled_at"] and u["id"] != me["id"]]
    for u in others:
        client.patch(f"/api/users/{u['id']}", json={"role": "operator"}, headers=auth)
    try:
        for change in ({"role": "operator"}, {"role": "viewer"}, {"disabled": True}):
            r = client.patch(f"/api/users/{me['id']}", json=change, headers=auth)
            assert r.status_code == 409, change
        assert client.get("/api/auth/me", headers=auth).json()["role"] == "admin"
    finally:
        for u in others:
            client.patch(f"/api/users/{u['id']}", json={"role": "admin"}, headers=auth)


def test_castor_admin_recovers_accounts(client, auth, capsys):
    from concurrent.futures import ThreadPoolExecutor

    from castor_server.manage import main as cli

    def main(argv):  # its own event loop, as from a shell
        with ThreadPoolExecutor(1) as pool:
            pool.submit(cli, argv).result()

    name = f"lost-{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": "viewer", "password": "forgotten1"}, headers=auth).json()
    client.patch(f"/api/users/{user['id']}", json={"disabled": True}, headers=auth)

    main(["reset-password", name, "--password", "remembered1"])
    assert "Reset the password" in capsys.readouterr().out
    r = client.post("/api/auth/login", json={"username": name, "password": "remembered1"})
    client.cookies.clear()
    assert r.status_code == 200  # and re-enabled

    main(["promote", name.upper()])  # usernames match case-insensitively
    assert next(u for u in client.get("/api/users", headers=auth).json() if u["id"] == user["id"])["role"] == "admin"

    main(["reset-password", name])  # no tty: generates and prints one
    generated = capsys.readouterr().out.rsplit(": ", 1)[1].strip()
    assert client.post("/api/auth/login", json={"username": name, "password": generated}).status_code == 200
    client.cookies.clear()

    main(["list-users"])
    assert name in capsys.readouterr().out
    client.patch(f"/api/users/{user['id']}", json={"role": "viewer", "disabled": True}, headers=auth)
