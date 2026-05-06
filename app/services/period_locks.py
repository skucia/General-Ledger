"""
Period-lock CRUD: querying current lock state, listing history, creating
new locks. The runtime validation chokepoint that REJECTS writes against
locked transactions lives in app/services/transactions.py — this module
is purely about managing the locks themselves.

Lock policy enforced by validate_lock_date() / create_lock():
  - locked_through must be on or before today (no future-dated locks)
  - locked_through must be strictly after any existing lock's date
    (locks can only move forward, never backward)
"""

from datetime import date
from typing import List, Optional

from psycopg.rows import dict_row

from app.db import get_connection


# --- Exceptions ------------------------------------------------------------

class LockDateInFutureError(Exception):
    """Raised when create_lock is called with locked_through > today."""

    def __init__(self, requested_date: date, today_date: date):
        self.requested_date = requested_date
        self.today_date = today_date
        super().__init__(
            f"Cannot create a lock for a future date. "
            f"The latest possible lock date is today ({today_date.strftime('%d/%m/%Y')})."
        )


class LockMustMoveForwardError(Exception):
    """Raised when create_lock is called with date <= the current lock."""

    def __init__(self, requested_date: date, current_date: date):
        self.requested_date = requested_date
        self.current_date = current_date
        super().__init__(
            f"Lock dates must move forward. The current lock is "
            f"{current_date.strftime('%d/%m/%Y')}; new locks must be after that."
        )


# --- Queries ---------------------------------------------------------------

def get_current_lock_date() -> Optional[date]:
    """
    Return the most recent locked_through date across all period_locks
    rows, or None if no locks exist. Used by the runtime chokepoint in
    transactions.py and by the lock-badge Jinja global in templating.py.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(locked_through) FROM period_locks")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


def list_locks() -> List[dict]:
    """
    Return all period_locks rows joined to users.username, most recent
    first. Used by the admin Period Locks page's history table.
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT pl.id,
                   pl.locked_through,
                   pl.locked_at,
                   pl.reason,
                   u.username AS locked_by_username
              FROM period_locks pl
              JOIN users u ON u.id = pl.locked_by
             ORDER BY pl.locked_through DESC, pl.locked_at DESC
            """
        )
        return cur.fetchall()


def count_transactions_through(through_date: date) -> int:
    """
    How many transactions are dated on or before `through_date`. Used
    by the confirmation step to surface the impact of the lock to the
    admin before they commit.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM transactions WHERE transaction_date <= %s",
            (through_date,),
        )
        return cur.fetchone()[0]


# --- Validation + create --------------------------------------------------

def validate_lock_date(locked_through: date) -> None:
    """
    Raise if `locked_through` can't be used as a new lock date.

    Same checks performed inside create_lock() — exposed publicly so the
    route handler can pre-validate during the form submission phase
    (before the confirmation step) and surface the same error message.
    """
    today = date.today()
    if locked_through > today:
        raise LockDateInFutureError(locked_through, today)

    current = get_current_lock_date()
    if current is not None and locked_through <= current:
        raise LockMustMoveForwardError(locked_through, current)


def create_lock(
    locked_through: date,
    locked_by: int,
    reason: Optional[str],
) -> int:
    """
    Insert a new period_locks row. Validates both lock-policy rules
    via validate_lock_date(), then performs the INSERT.

    Returns the new row id.
    """
    validate_lock_date(locked_through)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO period_locks (locked_through, locked_by, reason)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (locked_through, locked_by, reason or None),
        )
        return cur.fetchone()[0]
