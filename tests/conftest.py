"""
Test setup.

Two environment variables are set BEFORE the backend is imported:
  AGENT_DB_PATH   -> a throwaway SQLite file per test session
  AGENT_STEP_DELAY-> 0, so the suite runs fast and deterministically
"""

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="agent-tests-")
os.environ["AGENT_DB_PATH"] = os.path.join(_TMP_DIR, "test.db")
os.environ["AGENT_STEP_DELAY"] = "0"

import time  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import database  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture()
def client():
    """A fresh, empty database for every test."""
    if os.path.exists(os.environ["AGENT_DB_PATH"]):
        os.remove(os.environ["AGENT_DB_PATH"])
    database.reset_engine()
    with TestClient(app) as test_client:
        yield test_client


def new_key() -> str:
    return uuid.uuid4().hex


def wait_for_terminal(client, run_id: str, timeout: float = 10.0) -> dict:
    """
    Poll GET /runs/{id} until the run is completed or failed.

    This is exactly what the browser does, so the tests exercise the real path
    rather than calling the agent loop directly.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach a terminal state in {timeout}s")
