"""
The required test suite.

Test 1 - a successful run
Test 2 - the max-step limit stops a runaway agent
Test 3 - partial failure keeps earlier steps and credits
Test 4 - the same idempotency key never runs twice
Test 5 - different idempotency keys create different runs
Test 6 - runs survive a restart because they live in SQLite

Plus a few small API-contract tests at the bottom.
"""

import os

from fastapi.testclient import TestClient

from backend import database
from backend.config import MAX_STEPS
from backend.main import app
from backend.models import Run, Step

from .conftest import new_key, wait_for_terminal


def start_run(client, goal: str, key: str = None):
    """Helper: POST /runs and return (status_code, body)."""
    response = client.post(
        "/runs",
        json={"goal": goal},
        headers={"Idempotency-Key": key or new_key()},
    )
    return response.status_code, response.json()


# ---------------------------------------------------------------------------
# Test 1 - successful execution
# ---------------------------------------------------------------------------

def test_successful_run(client):
    status_code, created = start_run(client, "Research Python")
    assert status_code == 201
    assert created["status"] == "queued"          # execution happens in the background
    assert created["credits_used"] == 0

    run = wait_for_terminal(client, created["run_id"])

    assert run["status"] == "completed"
    assert run["output"]                          # a completed run always has output
    assert run["error"] is None

    # The default plan is planning -> search -> summarize: exactly 3 steps,
    # so exactly 3 credits.
    assert [s["name"] for s in run["steps"]] == ["planning", "search", "summarize"]
    assert all(s["status"] == "completed" for s in run["steps"])
    assert run["credits_used"] == 3
    assert run["credits_used"] == len(run["steps"])   # credits == steps executed
    assert run["current_step"] == 3


def test_analysis_goal_uses_a_longer_plan(client):
    """The mocked planner is deterministic but not a single hard-coded path."""
    _, created = start_run(client, "Analyze the Python ecosystem")
    run = wait_for_terminal(client, created["run_id"])

    assert run["status"] == "completed"
    assert [s["name"] for s in run["steps"]] == ["planning", "search", "analyze", "write_result"]
    assert run["credits_used"] == 4


# ---------------------------------------------------------------------------
# Test 2 - max-step protection
# ---------------------------------------------------------------------------

def test_max_steps_protection(client):
    """A goal whose plan is longer than MAX_STEPS must fail, not run forever."""
    _, created = start_run(client, "Research Python and never finish")
    run = wait_for_terminal(client, created["run_id"])

    assert run["status"] == "failed"
    assert run["error"] == "max_steps_exceeded"

    # The bound is real: no more than MAX_STEPS steps were executed or charged.
    assert len(run["steps"]) == MAX_STEPS
    assert run["credits_used"] == MAX_STEPS
    assert run["credits_used"] <= MAX_STEPS
    assert run["current_step"] <= MAX_STEPS
    assert run["output"] is None


def test_credits_stop_increasing_after_the_limit(client):
    """Once the run is terminal, nothing changes it again."""
    _, created = start_run(client, "Research Python and never finish")
    run = wait_for_terminal(client, created["run_id"])
    credits_at_failure = run["credits_used"]

    later = client.get(f"/runs/{created['run_id']}").json()
    assert later["credits_used"] == credits_at_failure
    assert later["status"] == "failed"


# ---------------------------------------------------------------------------
# Test 3 - partial tool failure
# ---------------------------------------------------------------------------

def test_partial_failure_preserves_state(client):
    """
    The writer tool fails on the LAST step, so steps 1 and 2 already succeeded.
    Everything about that partial progress must survive.
    """
    _, created = start_run(client, "Research Python and fail_writer")
    run = wait_for_terminal(client, created["run_id"])

    assert run["status"] == "failed"
    assert "tool_failed" in run["error"]

    steps = run["steps"]
    assert steps[0]["status"] == "completed"      # planning survived
    assert steps[1]["status"] == "completed"      # search survived
    assert steps[2]["status"] == "failed"         # the failing step is recorded
    assert steps[2]["error"]                      # with its reason
    assert steps[0]["detail"]                     # earlier results are still there

    # Credits are NOT refunded: 2 successful steps + 1 attempted step = 3.
    assert run["credits_used"] == 3
    assert run["output"] is None


def test_mid_run_tool_failure(client):
    """The search tool fails at step 2, so only 2 credits are consumed."""
    _, created = start_run(client, "Research Python and force failure")
    run = wait_for_terminal(client, created["run_id"])

    assert run["status"] == "failed"
    assert run["steps"][0]["status"] == "completed"
    assert run["steps"][1]["status"] == "failed"
    assert run["steps"][2]["status"] == "pending"   # never attempted, never charged
    assert run["credits_used"] == 2


def test_failed_run_can_be_retried_as_a_new_run(client):
    """Retrying does not mutate the old run - it creates a separate one."""
    _, first = start_run(client, "Research Python and fail_writer")
    failed = wait_for_terminal(client, first["run_id"])

    _, second = start_run(client, "Research Python")     # new key, new run
    succeeded = wait_for_terminal(client, second["run_id"])

    assert failed["run_id"] != succeeded["run_id"]
    assert succeeded["status"] == "completed"

    # The original failed run is untouched and still inspectable.
    still_there = client.get(f"/runs/{first['run_id']}").json()
    assert still_there["status"] == "failed"
    assert still_there["credits_used"] == 3


# ---------------------------------------------------------------------------
# Test 4 - idempotency
# ---------------------------------------------------------------------------

def test_same_idempotency_key_does_not_duplicate(client):
    key = new_key()

    first_status, first = start_run(client, "Research Python", key)
    assert first_status == 201
    finished = wait_for_terminal(client, first["run_id"])

    # The retry arrives after the run already finished.
    second_status, second = start_run(client, "Research Python", key)
    assert second_status == 200                     # 200 = replay, not 201 = created
    assert second["run_id"] == first["run_id"]
    assert second["credits_used"] == finished["credits_used"] == 3
    assert second["status"] == "completed"

    # And nothing kept executing afterwards.
    again = client.get(f"/runs/{first['run_id']}").json()
    assert again["credits_used"] == 3

    # Exactly one run exists in the database.
    session = database.SessionLocal()
    try:
        assert session.query(Run).count() == 1
    finally:
        session.close()


def test_replay_while_still_running_does_not_start_a_second_run(client):
    """The dangerous case: the retry arrives before the first run finished."""
    key = new_key()
    _, first = start_run(client, "Research Python", key)
    status_code, second = start_run(client, "Research Python", key)   # immediately

    assert status_code == 200
    assert second["run_id"] == first["run_id"]

    run = wait_for_terminal(client, first["run_id"])
    assert run["credits_used"] == 3                 # charged once, not twice

    session = database.SessionLocal()
    try:
        assert session.query(Run).count() == 1
    finally:
        session.close()


def test_same_key_with_a_different_goal_is_a_conflict(client):
    key = new_key()
    start_run(client, "Research Python", key)
    status_code, body = start_run(client, "Research Rust", key)

    assert status_code == 409
    assert body["error"] == "idempotency_key_reused"


# ---------------------------------------------------------------------------
# Test 5 - different keys create different runs
# ---------------------------------------------------------------------------

def test_different_keys_create_separate_runs(client):
    """Deduplication is keyed on the header, not on the goal text."""
    _, first = start_run(client, "Research Python", new_key())
    _, second = start_run(client, "Research Python", new_key())

    assert first["run_id"] != second["run_id"]

    a = wait_for_terminal(client, first["run_id"])
    b = wait_for_terminal(client, second["run_id"])
    assert a["credits_used"] == b["credits_used"] == 3

    session = database.SessionLocal()
    try:
        assert session.query(Run).count() == 2
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Test 6 - persistence across a restart
# ---------------------------------------------------------------------------

def test_run_survives_an_application_restart(client):
    _, created = start_run(client, "Research Python")
    finished = wait_for_terminal(client, created["run_id"])
    assert finished["status"] == "completed"

    # Simulate restarting the server: drop every connection and rebuild the
    # engine from the same SQLite file, then serve with a brand new client.
    database.reset_engine()

    with TestClient(app) as restarted:
        response = restarted.get(f"/runs/{created['run_id']}")
        assert response.status_code == 200
        run = response.json()

    assert run["run_id"] == created["run_id"]
    assert run["status"] == "completed"
    assert run["credits_used"] == 3
    assert len(run["steps"]) == 3
    assert run["output"] == finished["output"]

    # The idempotency protection survived the restart too.
    assert os.path.exists(os.environ["AGENT_DB_PATH"])


def test_interrupted_run_is_closed_out_on_restart(client):
    """
    A run that was executing when the process died must not stay 'running'
    forever - otherwise the browser polls it for eternity.

    We fake the crash by writing a half-finished run straight into SQLite,
    which is exactly the state a killed process leaves behind.
    """
    session = database.SessionLocal()
    try:
        session.add(
            Run(
                run_id="run_stranded",
                goal="Research Python",
                status="running",
                current_step=2,
                max_steps=MAX_STEPS,
                planned_steps=3,
                credits_used=2,
            )
        )
        session.flush()
        session.add(Step(run_id="run_stranded", step_number=1, name="planning", status="completed"))
        session.add(Step(run_id="run_stranded", step_number=2, name="search", status="running"))
        session.add(Step(run_id="run_stranded", step_number=3, name="summarize", status="pending"))
        session.commit()
    finally:
        session.close()

    # Restart the application: startup sweeps non-terminal runs.
    with TestClient(app) as restarted:
        run = restarted.get("/runs/run_stranded").json()

    assert run["status"] == "failed"
    assert run["error"] == "interrupted"
    assert run["steps"][1]["status"] == "failed"      # the in-flight step
    assert run["steps"][0]["status"] == "completed"   # earlier work preserved
    assert run["credits_used"] == 2                   # attempted work is not refunded


def test_idempotency_survives_a_restart(client):
    key = new_key()
    _, first = start_run(client, "Research Python", key)
    wait_for_terminal(client, first["run_id"])

    database.reset_engine()

    with TestClient(app) as restarted:
        response = restarted.post(
            "/runs", json={"goal": "Research Python"}, headers={"Idempotency-Key": key}
        )

    assert response.status_code == 200                  # replay, not a new run
    assert response.json()["run_id"] == first["run_id"]


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------

def test_missing_goal_is_rejected(client):
    response = client.post("/runs", json={}, headers={"Idempotency-Key": new_key()})
    assert response.status_code == 400
    assert "error" in response.json()


def test_empty_goal_is_rejected(client):
    response = client.post("/runs", json={"goal": "   "}, headers={"Idempotency-Key": new_key()})
    assert response.status_code == 400


def test_absurdly_long_goal_is_rejected(client):
    response = client.post(
        "/runs", json={"goal": "x" * 2001}, headers={"Idempotency-Key": new_key()}
    )
    assert response.status_code == 400


def test_missing_idempotency_key_is_rejected(client):
    response = client.post("/runs", json={"goal": "Research Python"})
    assert response.status_code == 400
    assert response.json()["error"] == "missing_idempotency_key"


def test_unknown_run_is_404(client):
    response = client.get("/runs/run_does_not_exist")
    assert response.status_code == 404
    assert response.json()["error"] == "run_not_found"


def test_no_stack_trace_is_ever_returned(client):
    """Error bodies are a fixed shape: {"error": ..., "message": ...}."""
    body = client.get("/runs/nope").json()
    assert set(body.keys()) == {"error", "message"}
    assert "Traceback" not in body["message"]
