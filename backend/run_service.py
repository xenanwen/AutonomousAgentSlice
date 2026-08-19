"""
The Run Service: everything that is a *rule* rather than an HTTP detail.

This layer sits between FastAPI and the agent. It owns:
  - creating a run (exactly once per Idempotency-Key)
  - kicking off execution
  - reading runs back

It deliberately knows nothing about HTTP status codes, and the agent loop
deliberately knows nothing about this file.
"""

import logging
import threading
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import database
from .agent.loop import execute_run, prepare_run
from .config import MAX_STEPS
from .models import IdempotencyRecord, Run, RunStatus, StepStatus, utcnow

logger = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    """The same Idempotency-Key was reused for a different goal (a client bug)."""


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def create_run(session: Session, goal: str, idempotency_key: str):
    """
    Create a run for this goal, or return the run this key already created.

    Returns (run, created) where `created` is False for a replayed request.

    Concurrency note - this is the important part:
    we do NOT do `if key not in db: create()`. Two simultaneous duplicate
    requests could both pass that check and both create a run. Instead we
    optimistically INSERT and let the PRIMARY KEY on idempotency_records
    reject the loser. The database's uniqueness constraint IS the lock, and
    it is atomic, so exactly one request can ever win.
    """
    # Fast path: we have already seen this key.
    existing = session.get(IdempotencyRecord, idempotency_key)
    if existing is not None:
        if existing.goal != goal:
            raise IdempotencyConflict(idempotency_key)
        return session.get(Run, existing.run_id), False

    run = Run(
        run_id=_new_run_id(),
        goal=goal,
        status=RunStatus.QUEUED,
        current_step=0,
        max_steps=MAX_STEPS,
        planned_steps=0,
        credits_used=0,
    )
    session.add(run)

    # Ask the planner for the plan and write the step rows now, so the browser
    # can show the intended steps as 'pending' before any work happens.
    prepare_run(session, run)

    # Push the run + steps to the database (still inside the open transaction)
    # so the idempotency record below has a row to point its foreign key at.
    session.flush()

    session.add(
        IdempotencyRecord(key=idempotency_key, run_id=run.run_id, goal=goal)
    )

    try:
        # Run + steps + idempotency record are committed as ONE transaction:
        # either all of it exists, or none of it does.
        session.commit()
    except IntegrityError:
        # Someone else inserted this key between our check and our commit.
        session.rollback()
        winner = session.get(IdempotencyRecord, idempotency_key)
        if winner is None:
            # The conflict was not about the idempotency key - re-raise so the
            # API layer can turn it into a 500 rather than hiding a real bug.
            raise
        if winner.goal != goal:
            raise IdempotencyConflict(idempotency_key)
        logger.info("idempotency race lost for key %s, returning existing run", idempotency_key)
        return session.get(Run, winner.run_id), False

    return run, True


def _execute_in_background(run_id: str) -> None:
    """Worker body: open a fresh Session for this thread and run the agent."""
    session = database.SessionLocal()
    try:
        execute_run(session, run_id)
    except Exception:
        # Never let a background thread die silently.
        logger.exception("background execution crashed for run %s", run_id)
    finally:
        session.close()


def start_run(run_id: str) -> None:
    """
    Start executing a run without blocking the HTTP request.

    Real agent runs take a long time, so the API is create-then-poll: POST /runs
    returns immediately with status 'queued' and the browser watches progress
    through GET /runs/{run_id}.

    A daemon thread is enough here - this is a single-process prototype, not a
    distributed job queue.
    """
    thread = threading.Thread(target=_execute_in_background, args=(run_id,), daemon=True)
    thread.start()


def recover_interrupted_runs(session: Session) -> int:
    """
    Clean up runs that were mid-flight when the process died.

    A run is executed by an in-memory worker thread. If the server is killed
    while a run is executing, that thread dies with it and nothing will ever
    move the run out of 'queued' or 'running' - the run would sit there
    forever and the browser would poll it forever.

    So on startup we sweep every non-terminal run and close it out as failed
    with error 'interrupted'. Credits already charged are NOT refunded, for
    the same reason a failed tool call is not refunded: the work was
    attempted. This guarantees the invariant that every run which is not
    actively executing is in a terminal state.

    Returns how many runs were recovered.
    """
    stranded = (
        session.query(Run)
        .filter(Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
        .all()
    )

    for run in stranded:
        for step in run.steps:
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.FAILED
                step.error = "interrupted: the server stopped while this step was running"
                step.finished_at = utcnow()
        run.status = RunStatus.FAILED
        run.error = "interrupted"
        run.updated_at = utcnow()

    if stranded:
        session.commit()
        logger.warning(
            "recovered %d run(s) that were interrupted by a restart: %s",
            len(stranded),
            ", ".join(run.run_id for run in stranded),
        )

    return len(stranded)


def get_run(session: Session, run_id: str):
    """Read one run (with its steps) back out of SQLite. None if it does not exist."""
    return session.get(Run, run_id)
