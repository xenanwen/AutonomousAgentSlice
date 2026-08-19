"""
SQLite connection setup.

Two things worth knowing here:

1. `check_same_thread=False` is required because the agent runs on a background
   worker thread while the web request that created it has already returned.
   Each thread still opens its OWN Session - we never share a Session between
   threads.

2. SQLite does not enforce foreign keys unless you ask it to, so we turn them
   on for every connection.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

Base = declarative_base()

# These are module-level so that `reset_engine()` can rebuild them (used by the
# persistence test, which simulates an application restart).
engine = None
SessionLocal = None


def _build_engine():
    """Create the engine + session factory from the currently configured DB path."""
    global engine, SessionLocal

    engine = create_engine(
        f"sqlite:///{config.DB_PATH}",
        # Allow the background worker thread to use connections from this engine.
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        """SQLite ignores FOREIGN KEY constraints unless this pragma is set."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine


_build_engine()


def init_db():
    """Create the tables if they do not exist yet. Safe to call on every startup."""
    from . import models  # noqa: F401  (importing registers the models on Base)

    Base.metadata.create_all(bind=engine)


def reset_engine():
    """
    Throw away the current connection pool and rebuild it from config.

    Used by the persistence test to prove that run data lives in the SQLite
    file rather than in the process's memory.
    """
    global engine
    if engine is not None:
        engine.dispose()
    _build_engine()
    init_db()


def get_session():
    """FastAPI dependency: one Session per request, always closed afterwards."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
