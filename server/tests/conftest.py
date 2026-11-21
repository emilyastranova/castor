import os
import shutil
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="castor-test-"))
# CASTOR_TEST_DATABASE_URL runs the suite against another database, e.g. an empty Postgres:
# postgresql+asyncpg://postgres:pw@localhost:55432/postgres
os.environ["CASTOR_DATABASE_URL"] = os.environ.get("CASTOR_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{_tmp / 'test.db'}"
os.environ["CASTOR_DB_NULL_POOL"] = "true"
os.environ["CASTOR_DATA_DIR"] = str(_tmp / "data")
os.environ["CASTOR_AGENT_SECRET"] = "test-agent-secret"
os.environ["CASTOR_ADMIN_USERNAME"] = "admin"
os.environ["CASTOR_ADMIN_PASSWORD"] = "secret"
os.environ["CASTOR_DISPATCH_INTERVAL"] = "0.2"
os.environ["CASTOR_PLUGIN_DIRECTORY_URL"] = ""  # tests never reach GitHub; test_plugin_directory sets its own
# Fake servers in the tests listen on loopback, which outbound requests may not reach by default.
os.environ["CASTOR_HTTP_ALLOW_ADDRESSES"] = "127.0.0.0/8"

# The library the tests sync: the real built-ins plus test-only fixtures (tests/fixtures: the
# demo commands and playbooks the end-to-end tests drive, which don't ship to users).
_repo, _fixtures = Path(__file__).resolve().parents[2], Path(__file__).resolve().parent / "fixtures"
for _kind in ("commands", "playbooks", "tools"):
    _dir = _tmp / "library" / _kind
    _dir.mkdir(parents=True)
    for _src in (_repo / _kind, _fixtures / _kind):
        for _file in sorted(_src.glob("*.y*ml")) if _src.is_dir() else []:
            shutil.copy(_file, _dir)
    os.environ[f"CASTOR_{_kind.upper()}_DIR"] = str(_dir)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from castor_server.app import app  # noqa: E402


def _watch_sqlite_writers() -> None:
    """When a test hits "database is locked", say who holds the lock: every connection with an open
    write transaction, the asyncio task that opened it and where that task is now. (A flaky CI
    failure where something never let go; this names it the next time it happens.)"""
    import asyncio
    import logging
    import traceback

    from sqlalchemy import event

    from castor_server.db import engine

    if engine.dialect.name != "sqlite":
        return
    log = logging.getLogger("castor.tests.sqlite")
    writers: dict[int, tuple[asyncio.Task | None, str]] = {}

    def task_now() -> asyncio.Task | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _write(conn, cursor, statement, parameters, context, executemany) -> None:
        key = id(conn.connection.dbapi_connection)
        if key not in writers and statement.lstrip()[:6].upper() in ("INSERT", "UPDATE", "DELETE"):
            task = task_now()
            writers[key] = (task, f"{statement.split(chr(10))[0][:120]} (from {task.get_name() if task else '?'})")

    @event.listens_for(engine.sync_engine, "commit")
    @event.listens_for(engine.sync_engine, "rollback")
    @event.listens_for(engine.sync_engine, "close")
    def _done(conn, *args) -> None:
        dbapi = getattr(conn, "connection", None)
        writers.pop(id(getattr(dbapi, "dbapi_connection", dbapi)), None)

    @event.listens_for(engine.sync_engine, "handle_error")
    def _locked(ctx) -> None:
        if "database is locked" not in str(ctx.original_exception):
            return
        lines = [f"database is locked; {len(writers)} open write transaction(s):"]
        for task, first in list(writers.values()):
            lines.append(f"- first write: {first}")
            if task is not None and not task.done():
                lines.append("  where that task is now:\n" + "".join(
                    "    " + line for frame in task.get_stack(limit=12) for line in traceback.format_stack(frame, limit=1)))
            elif task is not None:
                lines.append("  (that task has finished: its connection was never committed, rolled back or closed)")
        log.error("\n".join(lines))


_watch_sqlite_writers()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "secret"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    # Login also sets a session cookie on the shared client; drop it so every test
    # authenticates explicitly (and "not logged in" tests stay meaningful).
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}
