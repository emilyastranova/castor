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
