"""
Tiny database helper around psycopg v3.

For a beginner-friendly app we don't need a connection pool yet — we open
a fresh connection per request/script. Later we can swap `get_connection()`
for a pooled version without touching callers.

Usage:
    from app.db import get_connection
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        print(cur.fetchone())
"""

from contextlib import contextmanager
from typing import Iterator

import psycopg

from app.config import settings


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    """
    Open a Postgres connection using the DSN from .env. The `with` block
    auto-commits on clean exit and rolls back if an exception is raised.
    """
    conn = psycopg.connect(settings.db_dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
