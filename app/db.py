"""
Tiny database helper around psycopg v3.

`get_connection()` opens a fresh per-call connection. The DSN it picks is
driven by a `contextvars.ContextVar` set per-request (by the middleware
in app/main.py) or per-block (by `set_active_db()` for explicit code
paths like the /login user lookup). When the ContextVar is unset, we
fall back to the .env-configured DSN so that system scripts (migrations,
create_admin) work unchanged.

When we eventually need real connection pools (e.g. once we have
concurrent users in the cloud), the change should be isolated to this
file: replace `psycopg.connect(dsn)` with a per-DSN pool checkout.

Usage from app code:
    from app.db import get_connection
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")

Usage when you need to override the active DB explicitly (e.g. logging in):
    from app.db import set_active_db
    with set_active_db("test"):
        user = users_service.get_user_by_username(name)
"""

import contextvars
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg

from app.config import DATABASES, settings


# Per-task active database key ('test' / 'live'). None = fall back to .env.
# ContextVar is task-local so concurrent FastAPI requests can't leak into
# each other.
_active_db: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "active_db_key", default=None
)


def get_active_db() -> Optional[str]:
    """Return the current ContextVar value, or None if unset."""
    return _active_db.get()


@contextmanager
def set_active_db(db_key: str) -> Iterator[None]:
    """
    Set the active database key for the duration of the `with` block.
    Used by the /login route to run the user lookup against the
    user-selected database before the session has been established.
    """
    if db_key not in DATABASES:
        raise ValueError(
            f"Unknown database key: {db_key!r}. Allowed: {sorted(DATABASES)}"
        )
    token = _active_db.set(db_key)
    try:
        yield
    finally:
        _active_db.reset(token)


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    """
    Open a Postgres connection. Routes to:
      1. The ContextVar-selected database, if set (web requests, /login lookup)
      2. .env's DB_NAME, if not (system scripts, app startup)
    Auto-commits on clean exit, rolls back on exception.
    """
    db_key = _active_db.get()
    dsn = settings.db_dsn_for(db_key) if db_key else settings.db_dsn
    conn = psycopg.connect(dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
