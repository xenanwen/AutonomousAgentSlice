"""
The agent loop: the heart of the system.

Conceptually it is nothing more than:

    while there is another step and we still have budget:
        charge the credit
        execute the step
        record the result
        stop immediately if it failed

Everything else in this file is bookkeeping so that a browser polling
GET /runs/{id} can see exactly where the agent is right now, and so that a
failed run stays inspectable afterwards.
"""

import logging
import time
from typing import Dict, List

from sqlalchemy.orm import Session

from ..config import STEP_COST, STEP_DELAY_SECONDS
from ..models import Run, RunStatus, Step, StepStatus, utcnow
from . import planner
from .tools import ToolError, fake_search, fake_writer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preparing a run
# ---------------------------------------------------------------------------

def prepare_run(session: Session, run: Run) -> None:
    """
    Ask the planner for a plan and persist one Step row per executable step.

    We remember how many steps the planner WANTED (`planned_steps`) but only
    create rows for the ones we are allowed to execute (`max_steps`). If the
    planner wanted more than the budget allows, the run is doomed to end in
    max_steps_exceeded - and we can prove it by comparing the two numbers.
    """
    proposed: List[str] = planner.plan(run.goal)
    run.planned_steps = len(proposed)

    executable = proposed[: run.max_steps]
    for index, name in enumerate(executable, start=1):
        session.add(
            Step(
                run_id=run.run_id,
                step_number=index,
                name=name,
                status=StepStatus.PENDING,
            )
        )


# ---------------------------------------------------------------------------
# Executing one step
# ---------------------------------------------------------------------------

def execute_step(name: str, context: Dict[str, object]) -> str:
    """
    Perform a single step and return a short human-readable detail string.

    `context` is the agent's explicit working memory. It is passed in and out
    rather than kept in globals, so what a step can see is always obvious.

    Raises ToolError when a tool fails - the loop turns that into a failed run.
    """
    goal = str(context["goal"])

    if name == planner.STEP_PLANNING:
        steps = " -> ".join(context["plan"])  # type: ignore[arg-type]
        return f"Planned {len(context['plan'])} steps: {steps}"  # type: ignore[arg-type]

    if name == planner.STEP_SEARCH:
        # The only place an external-ish dependency is touched.
        response = fake_search(goal)
        results = list(response["results"])  # type: ignore[arg-type]
        findings: List[str] = context.setdefault("findings", [])  # type: ignore[assignment]
        findings.extend(results)
        return f"Search returned {len(results)} results for '{response['query']}'."

    if name == planner.STEP_ANALYZE:
        findings: List[str] = context.get("findings", [])  # type: ignore[assignment]
        key_points = [f"- {line}" for line in findings[:3]]
        context["analysis"] = "\n".join(key_points)
        return f"Analyzed {len(findings)} findings into {len(key_points)} key points."

    if name in (planner.STEP_SUMMARIZE, planner.STEP_WRITE_RESULT):
        findings: List[str] = context.get("findings", [])  # type: ignore[assignment]
        output = fake_writer(goal, findings)
        context["output"] = output
        return f"Wrote the final answer ({len(output)} characters)."

    # Defensive: an unknown step name is a programming error, not a user error.
    raise ToolError(f"Unknown step '{name}'")


# ---------------------------------------------------------------------------
# Executing a whole run
# ---------------------------------------------------------------------------

def _fail_run(session: Session, run: Run, error: str) -> None:
    """Move a run into the terminal FAILED state, keeping everything else intact."""
    run.status = RunStatus.FAILED
    run.error = error
    run.updated_at = utcnow()
    session.commit()
    logger.warning("run %s failed: %s", run.run_id, error)


def execute_run(session: Session, run_id: str) -> Run:
    """
    Run the agent loop for one run, committing after every state change.

    Committing frequently is what makes progress visible to the polling
    frontend, and what makes a crash mid-run leave behind an accurate record
    instead of a lie.
    """
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"run {run_id} does not exist")

    # A completed or failed run is terminal: never execute it a second time.
    # This is the last line of defence against a duplicate request slipping
    # through and re-charging a client.
    if run.is_terminal() or run.status == RunStatus.RUNNING:
        return run

    run.status = RunStatus.RUNNING
    run.updated_at = utcnow()
    session.commit()

    # Explicit working memory for this run.
    context: Dict[str, object] = {
        "goal": run.goal,
        "plan": [step.name for step in run.steps],
        "findings": [],
    }

    for step in run.steps:
        # --- Bound check -------------------------------------------------
        # Belt and braces: the loop can only iterate over rows we created
        # (already capped at max_steps), but we re-check anyway because this
        # is the single most important safety property of the system.
        if run.current_step >= run.max_steps:
            _fail_run(session, run, "max_steps_exceeded")
            return run

        # --- Charge the credit BEFORE doing the work ---------------------
        # Policy: a step that is attempted is a step that is paid for, even if
        # the tool then fails. Integers only, so the total is always exact.
        run.current_step = step.step_number
        run.credits_used += STEP_COST

        step.status = StepStatus.RUNNING
        step.started_at = utcnow()
        run.updated_at = utcnow()
        session.commit()

        # Small pause so a human can watch the steps appear in the browser.
        if STEP_DELAY_SECONDS:
            time.sleep(STEP_DELAY_SECONDS)

        # --- Do the work -------------------------------------------------
        try:
            detail = execute_step(step.name, context)
        except ToolError as exc:
            # Partial failure: this step failed, but every earlier step and
            # every credit already spent stays exactly as it is.
            step.status = StepStatus.FAILED
            step.error = str(exc)
            step.finished_at = utcnow()
            session.commit()
            _fail_run(session, run, f"tool_failed: {exc}")
            return run
        except Exception:  # unexpected bug in our own code
            logger.exception("unexpected error in step %s of run %s", step.name, run.run_id)
            step.status = StepStatus.FAILED
            step.error = "internal_error"
            step.finished_at = utcnow()
            session.commit()
            _fail_run(session, run, "internal_error")
            return run

        step.status = StepStatus.COMPLETED
        step.detail = detail
        step.finished_at = utcnow()
        run.updated_at = utcnow()
        session.commit()

    # --- Loop finished -----------------------------------------------------
    # We executed every step we were allowed to. Did the PLAN actually finish?
    if run.planned_steps > run.max_steps:
        _fail_run(session, run, "max_steps_exceeded")
        return run

    output = context.get("output")
    if not output:
        # Invariant: a completed run always has an output. If we somehow got
        # here without one, that is a failure, not a success.
        _fail_run(session, run, "internal_error: run produced no output")
        return run

    run.status = RunStatus.COMPLETED
    run.output = str(output)
    run.error = None
    run.updated_at = utcnow()
    session.commit()
    return run
