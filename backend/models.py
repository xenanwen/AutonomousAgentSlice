"""
The database tables: runs, steps, idempotency_records.

The constraints in here are the real safety net. Application code can have
bugs; a CHECK constraint cannot be talked out of its opinion.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow() -> datetime:
    """Single source of 'now' so timestamps are consistent and timezone-aware."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Explicit state vocabularies.
# Plain string constants keep the values readable in the database and in JSON.
# ---------------------------------------------------------------------------

class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    TERMINAL = (COMPLETED, FAILED)
    ALL = (QUEUED, RUNNING, COMPLETED, FAILED)


class StepStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    ALL = (PENDING, RUNNING, COMPLETED, FAILED)


class Run(Base):
    """One actual execution of the agent."""

    __tablename__ = "runs"

    run_id = Column(String, primary_key=True)          # unique by being the PK
    goal = Column(Text, nullable=False)
    status = Column(String, nullable=False, default=RunStatus.QUEUED)

    # How many steps have been started so far (also = how many credits charged).
    current_step = Column(Integer, nullable=False, default=0)
    # The hard bound copied onto the row, so an old run stays auditable even if
    # the global MAX_STEPS policy changes later.
    max_steps = Column(Integer, nullable=False)
    # How many steps the planner wanted. If this is larger than max_steps the
    # run cannot possibly finish, which is how we detect max_steps_exceeded.
    planned_steps = Column(Integer, nullable=False, default=0)

    credits_used = Column(Integer, nullable=False, default=0)

    output = Column(Text, nullable=True)   # set only on success
    error = Column(Text, nullable=True)    # set only on failure

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    steps = relationship(
        "Step",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Step.step_number",
    )

    __table_args__ = (
        # Impossible states the database itself refuses to store.
        CheckConstraint("credits_used >= 0", name="ck_credits_non_negative"),
        CheckConstraint("credits_used <= max_steps", name="ck_credits_within_bound"),
        CheckConstraint("current_step >= 0", name="ck_current_step_non_negative"),
        CheckConstraint("current_step <= max_steps", name="ck_current_step_bounded"),
        CheckConstraint(
            "status in ('queued','running','completed','failed')",
            name="ck_run_status_valid",
        ),
    )

    def is_terminal(self) -> bool:
        return self.status in RunStatus.TERMINAL


class Step(Base):
    """One step inside a run. Persisted so progress survives a crash or restart."""

    __tablename__ = "steps"

    step_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_number = Column(Integer, nullable=False)   # 1-based
    name = Column(String, nullable=False)           # "planning", "search", ...
    status = Column(String, nullable=False, default=StepStatus.PENDING)

    detail = Column(Text, nullable=True)   # short human-readable result
    error = Column(Text, nullable=True)    # set only when this step failed

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    run = relationship("Run", back_populates="steps")

    __table_args__ = (
        # A run can never have two step 3s.
        UniqueConstraint("run_id", "step_number", name="uq_step_number_per_run"),
        CheckConstraint("step_number >= 1", name="ck_step_number_positive"),
        CheckConstraint(
            "status in ('pending','running','completed','failed')",
            name="ck_step_status_valid",
        ),
    )


class IdempotencyRecord(Base):
    """
    Maps a client's Idempotency-Key to the run it created.

    The key is the PRIMARY KEY, so it is unique at the database level. That
    uniqueness - not an application-level "if not exists" check - is what makes
    two simultaneous duplicate requests safe.
    """

    __tablename__ = "idempotency_records"

    key = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("runs.run_id"), nullable=False)
    # Stored so we can detect a client reusing one key for a different request.
    goal = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    # Declaring the relationship (not just the ForeignKey column) is what tells
    # SQLAlchemy to INSERT the run before the idempotency record that points
    # at it. Without it the two inserts can be emitted in the wrong order and
    # the foreign key constraint fails.
    run = relationship("Run")
