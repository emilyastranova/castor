import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="castor-test-"))
os.environ["CASTOR_DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp / 'test.db'}"
os.environ["CASTOR_DATA_DIR"] = str(_tmp / "data")
os.environ["CASTOR_AGENT_SECRET"] = "test-agent-secret"
os.environ["CASTOR_ADMIN_USERNAME"] = "admin"
os.environ["CASTOR_ADMIN_PASSWORD"] = "secret"
os.environ["CASTOR_DISPATCH_INTERVAL"] = "0.2"

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
    return {"Authorization": f"Bearer {token}"}
