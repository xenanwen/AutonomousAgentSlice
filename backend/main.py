"""
The HTTP layer.

This file only does HTTP things: read the request, validate it, call the run
service, choose a status code, and shape the response. No agent logic lives
here.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import database, run_service
from .database import get_session, init_db
from .schemas import CreateRunRequest, RunResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent-api")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Startup work, before the first request is served:
      1. create the SQLite tables if they do not exist
      2. close out any run that was still executing when the process last died
    """
    init_db()

    # Looked up through the module (not imported by name) so that rebuilding
    # the engine - as the persistence test does - is picked up here too.
    session = database.SessionLocal()
    try:
        recovered = run_service.recover_interrupted_runs(session)
        if recovered:
            logger.warning("marked %d interrupted run(s) as failed on startup", recovered)
    finally:
        session.close()

    yield


app = FastAPI(
    title="Autonomous Agent Slice",
    version="1.0.0",
    description="A minimal but real slice of an autonomous agent: bounded execution, "
                "exact credit accounting, durable state and safe retries.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Error handling: the client gets a clean JSON shape, the server logs detail.
# A stack trace must never reach the browser.
# ---------------------------------------------------------------------------

def _error(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "message": message})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    """Malformed body -> 400 with a readable message instead of FastAPI's 422 blob."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", []) if part != "body") or "body"
    reason = first.get("msg", "invalid request")
    return _error(status.HTTP_400_BAD_REQUEST, "invalid_request", f"{field}: {reason}")


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"error": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception):
    logger.exception("unhandled error: %s", exc)
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "The server hit an unexpected error. Check the server logs for details.",
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: CreateRunRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
):
    """
    Start an agent run.

    Requires an `Idempotency-Key` header. Sending the same key twice returns the
    same run and does NOT execute the agent again or charge credits again.

    Returns 201 for a newly created run and 200 for a replayed request, so the
    client can tell the difference.
    """
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_idempotency_key",
                "message": "An 'Idempotency-Key' header is required so that retried "
                           "requests do not create duplicate runs.",
            },
        )

    try:
        run, created = run_service.create_run(session, payload.goal, idempotency_key.strip())
    except run_service.IdempotencyConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "idempotency_key_reused",
                "message": "This Idempotency-Key was already used for a different goal. "
                           "Use a new key for a new request.",
            },
        )

    # Serialise BEFORE handing the run to the worker thread, so the response is
    # a clean snapshot rather than a half-updated row.
    body = RunResponse.model_validate(run)

    if created:
        run_service.start_run(run.run_id)
        response.status_code = status.HTTP_201_CREATED
    else:
        # Replay: no new run, no new execution, no new credits.
        response.status_code = status.HTTP_200_OK

    return body


@app.get("/runs/{run_id}", response_model=RunResponse)
def read_run(run_id: str, session: Session = Depends(get_session)):
    """Return the persisted run and its steps. This is what the frontend polls."""
    run = run_service.get_run(session, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "run_not_found", "message": f"No run with id '{run_id}'."},
        )
    return RunResponse.model_validate(run)


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve the frontend from the same origin as the API (so no CORS setup needed).
# Mounted last so it never shadows an API route.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
