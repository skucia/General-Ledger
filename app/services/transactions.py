"""
Transaction-posting service.

post_transaction() is the only write path for new transactions. It runs
all INSERTs (header + lines) inside a single connection transaction.
The DR=CR balance trigger is DEFERRABLE INITIALLY DEFERRED, so it
doesn't fire until COMMIT — which means the app can insert the header
and N lines and the trigger sees the final, complete picture once.

reverse_transaction() posts a reversing entry against an existing
transaction. It calls post_transaction() internally so the period-lock
chokepoint applies automatically.

Period locking:
- The single chokepoint _assert_period_open() runs at the top of
  post_transaction() and raises PeriodLockedError on a friendly
  message if the date is closed.
- Migration 006 also adds a DB-level trigger as a safety net for any
  future write paths or direct SQL that bypass this code.

If something goes wrong (a trigger raises, an FK fails, anything else),
the connection helper rolls back automatically, so partial transactions
can never end up in the DB.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, TypedDict

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.db import get_connection
# get_current_lock_date moved to app.services.period_locks (separation of
# concerns — that module owns lock CRUD; we just consume the current value
# inside the chokepoint).
from app.services.period_locks import get_current_lock_date


class TransactionLineInput(TypedDict):
    """Shape of one validated line passed into post_transaction."""

    dr_cr: str            # 'DR' or 'CR'
    account_number: str   # canonical (already uppercased + verified to exist)
    amount: Decimal       # positive, two-decimal-quantized


class UnbalancedTransactionError(Exception):
    """
    Raised if the DB-level balance trigger rejects the post. Should never
    happen if the route handler validated correctly, but it's a fail-safe.
    """


class TransactionNotFoundError(Exception):
    """Raised when a transaction id doesn't exist."""


class TransactionNotEditableError(Exception):
    """
    Raised when a transaction exists but fails the editability guards
    (system-generated entry, a reversal, already reversed, etc.). Carries
    a user-facing message.
    """


class PeriodLockedError(Exception):
    """
    Raised when a transaction date falls in a locked period. Carries the
    date that was rejected, the lock_through date, and a verb describing
    the operation that triggered the rejection (post / edit / delete /
    reverse) so the message can be rendered cleanly to the user.
    """

    def __init__(
        self,
        transaction_date: date,
        lock_date: date,
        operation: str = "post",
    ):
        self.transaction_date = transaction_date
        self.lock_date = lock_date
        self.operation = operation
        verb = {
            "post": "post",
            "edit": "edit",
            "delete": "delete",
            "reverse": "reverse",
        }.get(operation, operation)
        super().__init__(
            f"Cannot {verb} transaction dated {transaction_date.strftime('%d/%m/%Y')} "
            f"— period is locked through {lock_date.strftime('%d/%m/%Y')}"
        )


# --- Period-lock chokepoint -----------------------------------------------
# get_current_lock_date is imported from app.services.period_locks. The
# function below is THE single chokepoint that every transaction write
# path must call. The DB trigger in migration 006 is the safety net.

def assert_period_open(transaction_date: date, operation: str = "post") -> None:
    """
    THE single chokepoint for the period-lock check. Every transaction
    write path MUST call this before issuing the SQL. Raises
    PeriodLockedError if `transaction_date` is on or before the current
    lock date.

    Public so other services (year_end_close) can call it as a defensive
    pre-insert check inside their own atomic blocks.

    The DB trigger in migration 006 is the safety net for any code path
    that bypasses this function (direct SQL, future write paths added
    without remembering the check, etc).
    """
    lock_date = get_current_lock_date()
    if lock_date is not None and transaction_date <= lock_date:
        raise PeriodLockedError(transaction_date, lock_date, operation)


# --- Insertion primitive --------------------------------------------------

def insert_transaction_within(
    cur,
    *,
    transaction_date: date,
    description: str,
    transaction_reference: str,
    created_by: int,
    lines: List[TransactionLineInput],
    reverses_transaction_id: Optional[int] = None,
    journal_type: str = "STANDARD",
) -> int:
    """
    Insert a transaction header + lines using the GIVEN cursor. The
    caller owns the connection and is responsible for commit / rollback.

    Used by:
      - post_transaction()                — public single-transaction posting
      - year_end_close.execute_close()    — runs in an atomic block that
        also inserts into period_locks within the same DB transaction

    Does NOT call assert_period_open(); callers must invoke it before
    using this helper. The DB trigger (migration 006) is the safety net
    against bypass.
    """
    cur.execute(
        """
        INSERT INTO transactions (
            transaction_date,
            description,
            transaction_reference,
            created_by,
            reverses_transaction_id,
            journal_type
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            transaction_date,
            description,
            transaction_reference,
            created_by,
            reverses_transaction_id,
            journal_type,
        ),
    )
    txn_id = cur.fetchone()[0]

    for line in lines:
        cur.execute(
            """
            INSERT INTO transaction_lines (
                transaction_id, dr_cr, account_number, amount
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                txn_id,
                line["dr_cr"],
                line["account_number"],
                line["amount"],
            ),
        )
    return txn_id


# --- Posting --------------------------------------------------------------

def post_transaction(
    transaction_date: date,
    description: str,
    transaction_reference: str,
    created_by: int,
    lines: List[TransactionLineInput],
    reverses_transaction_id: Optional[int] = None,
    attachments: Optional[List[dict]] = None,
) -> int:
    """
    Insert one transaction header + its lines atomically. Returns the new
    transaction id. Always inserts with journal_type='STANDARD' — the
    Year-End Close service uses insert_transaction_within() directly to
    pass 'YEAR_END_CLOSE'.

    `attachments` is an optional list of {"path", "original_name"} dicts
    (files already written to disk by the caller). Each becomes a row in
    transaction_attachments within the same DB transaction, so they commit
    atomically with the header + lines. More can be added later via the
    attachments service (the 5-per-transaction cap is enforced there and
    by the caller at entry time).

    Raises:
      PeriodLockedError          — date falls in a locked period
      UnbalancedTransactionError — DR sum != CR sum (DB trigger at COMMIT)
    """
    # Chokepoint — every transaction write goes through this.
    assert_period_open(transaction_date, operation="post")

    try:
        with get_connection() as conn, conn.cursor() as cur:
            txn_id = insert_transaction_within(
                cur,
                transaction_date=transaction_date,
                description=description,
                transaction_reference=transaction_reference,
                created_by=created_by,
                lines=lines,
                reverses_transaction_id=reverses_transaction_id,
                journal_type="STANDARD",
            )
            # Optional post-time attachments. Written into the
            # transaction_attachments child table within the SAME DB
            # transaction so they commit atomically with the header + lines.
            for att in (attachments or []):
                cur.execute(
                    """
                    INSERT INTO transaction_attachments
                        (transaction_id, attachment_path,
                         attachment_original_name, uploaded_by)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        txn_id,
                        att["path"],
                        att["original_name"] or att["path"],
                        created_by,
                    ),
                )
            # COMMIT happens here as the context manager exits. The deferred
            # balance trigger fires at COMMIT — if the totals don't match,
            # Postgres raises and our context manager rolls back.
        return txn_id
    except psycopg.errors.DatabaseError as exc:
        # Two triggers can raise here:
        #   - period-lock trigger (immediate, on INSERT) — SQLSTATE 'GL001',
        #     surfaces as a generic psycopg.DatabaseError (custom SQLSTATE
        #     classes don't map to specific psycopg subclasses)
        #   - balance trigger (deferred, on COMMIT) — default SQLSTATE 'P0001',
        #     surfaces as psycopg.errors.RaiseException (a DatabaseError subclass)
        # Catching DatabaseError covers both; dispatch by sqlstate.
        if exc.sqlstate == "GL001":
            # Race: a lock row was added between _assert_period_open and the
            # INSERT. Re-fetch the current lock date for the message.
            current_lock = get_current_lock_date() or transaction_date
            raise PeriodLockedError(transaction_date, current_lock, "post") from exc
        if exc.sqlstate == "P0001":
            # Default-code RAISE EXCEPTION — only the balance trigger uses this.
            raise UnbalancedTransactionError(str(exc)) from exc
        # Unknown DB-side error — propagate so it's not silently swallowed.
        raise


# --- Reversal -------------------------------------------------------------

def reverse_transaction(
    transaction_id: int,
    requested_reversal_date: date,
    reason: str,
    created_by: int,
) -> dict:
    """
    Post a reversing entry for `transaction_id`. The new transaction has
    flipped DR/CR on every line and a reverses_transaction_id FK back
    to the original.

    Date handling: if `requested_reversal_date` falls in a locked period,
    automatically forward to (lock_date + 1 day) so the reversal lands
    in the next open period. The function calls post_transaction()
    internally — so the period-lock check applies to the resolved date,
    and any race-condition lock added between forward-calc and the
    insert is caught by the DB trigger.

    Returns:
      {
        "txn_id": int,            # id of the new reversal transaction
        "reversal_date": date,    # actual date used (may differ from requested)
        "date_was_forwarded": bool,
        "message": str | None,    # user-facing note explaining any forward
      }
    """
    # Step 1: resolve the actual reversal date, forwarding past any lock.
    lock_date = get_current_lock_date()
    if lock_date is not None and requested_reversal_date <= lock_date:
        actual_date = lock_date + timedelta(days=1)
        date_was_forwarded = True
        message = (
            f"Reversal date forwarded to {actual_date.strftime('%d/%m/%Y')} "
            f"because the period is locked through {lock_date.strftime('%d/%m/%Y')}."
        )
    else:
        actual_date = requested_reversal_date
        date_was_forwarded = False
        message = None

    # Step 2: fetch the original header + lines.
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT transaction_date, transaction_reference, description
              FROM transactions
             WHERE id = %s
            """,
            (transaction_id,),
        )
        original = cur.fetchone()
        if original is None:
            raise ValueError(f"Transaction {transaction_id} not found")

        cur.execute(
            """
            SELECT id, dr_cr, account_number, amount
              FROM transaction_lines
             WHERE transaction_id = %s
             ORDER BY id
            """,
            (transaction_id,),
        )
        original_lines = cur.fetchall()

    if not original_lines:
        raise ValueError(f"Transaction {transaction_id} has no lines to reverse")

    # Step 3: build the reversed lines (flip DR <-> CR).
    reversed_lines: List[TransactionLineInput] = [
        {
            "dr_cr": "CR" if l["dr_cr"] == "DR" else "DR",
            "account_number": l["account_number"],
            "amount": l["amount"],
        }
        for l in original_lines
    ]

    # Step 4: compose reference + description, truncated to fit columns.
    new_ref = f"REV-{original['transaction_reference']}"[:20]
    new_desc = f"Reversal of {original['transaction_reference']}: {reason}"[:200]

    # Step 5: post via the chokepoint. The lock check inside
    # post_transaction sees the resolved (forwarded) date.
    new_txn_id = post_transaction(
        transaction_date=actual_date,
        description=new_desc,
        transaction_reference=new_ref,
        created_by=created_by,
        lines=reversed_lines,
        reverses_transaction_id=transaction_id,
    )

    return {
        "txn_id": new_txn_id,
        "reversal_date": actual_date,
        "date_was_forwarded": date_was_forwarded,
        "message": message,
    }


# --- Editing (open-period, STANDARD transactions only) --------------------

def _assert_editable(cur, transaction_id: int) -> dict:
    """
    Fetch a transaction header (using the GIVEN dict_row cursor) and verify
    it passes the editability guards. Returns the header row. Raises
    TransactionNotFoundError or TransactionNotEditableError.

    Does NOT check the period lock — callers use assert_period_open()
    separately so the message can name the date. Using the caller's cursor
    means the guard and the subsequent edit share one DB snapshot.

    Only plain STANDARD transactions are editable. System journals
    (year-end close), reversal entries, and transactions that have already
    been reversed are off-limits to preserve ledger integrity.
    """
    cur.execute(
        """
        SELECT t.id,
               t.transaction_date,
               t.description,
               t.transaction_reference,
               t.journal_type,
               t.reverses_transaction_id,
               EXISTS (
                   SELECT 1 FROM transactions r
                    WHERE r.reverses_transaction_id = t.id
               ) AS is_reversed
          FROM transactions t
         WHERE t.id = %s
        """,
        (transaction_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise TransactionNotFoundError(f"Transaction {transaction_id} not found")
    if row["journal_type"] != "STANDARD":
        raise TransactionNotEditableError(
            "This is a system-generated journal (e.g. a year-end close) "
            "and can't be edited."
        )
    if row["reverses_transaction_id"] is not None:
        raise TransactionNotEditableError(
            "This is a reversal entry and can't be edited — adjust the "
            "original transaction instead."
        )
    if row["is_reversed"]:
        raise TransactionNotEditableError(
            "This transaction has already been reversed and can't be edited."
        )
    return row


def get_transaction_for_edit(transaction_id: int) -> dict:
    """
    Fetch a transaction (header + lines) for the edit form. Enforces the
    editability guards and the period lock (a locked-period transaction
    can't be edited). Returns:
      {
        "id", "transaction_date" (date), "description",
        "transaction_reference",
        "lines": [{"dr_cr", "account_number", "amount" (Decimal)}, ...],
      }
    Raises TransactionNotFoundError, TransactionNotEditableError,
    PeriodLockedError.
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        header = _assert_editable(cur, transaction_id)
        assert_period_open(header["transaction_date"], operation="edit")
        cur.execute(
            """
            SELECT dr_cr, account_number, amount
              FROM transaction_lines
             WHERE transaction_id = %s
             ORDER BY id
            """,
            (transaction_id,),
        )
        lines = cur.fetchall()
    return {
        "id": header["id"],
        "transaction_date": header["transaction_date"],
        "description": header["description"],
        "transaction_reference": header["transaction_reference"],
        "lines": [
            {
                "dr_cr": l["dr_cr"],
                "account_number": l["account_number"],
                "amount": l["amount"],
            }
            for l in lines
        ],
    }


def update_transaction(
    transaction_id: int,
    *,
    transaction_date: date,
    description: str,
    transaction_reference: str,
    lines: List[TransactionLineInput],
    edited_by: int,
    reason: Optional[str] = None,
) -> None:
    """
    Edit an existing open-period STANDARD transaction in place. Captures a
    full before-snapshot (header + lines) into transaction_edits, UPDATEs
    the header, and replaces the lines wholesale — all in ONE DB
    transaction, so the deferred balance trigger validates the new lines at
    COMMIT.

    Both the current date and the new date must be in the open period.

    Raises TransactionNotFoundError, TransactionNotEditableError,
    PeriodLockedError, UnbalancedTransactionError.
    """
    # New date must be in the open period (the current date is checked
    # below, once we've read the row).
    assert_period_open(transaction_date, operation="edit")

    try:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            header = _assert_editable(cur, transaction_id)
            # The CURRENT (pre-edit) date must also be open — can't touch a
            # locked-period row even to move it forward.
            assert_period_open(header["transaction_date"], operation="edit")

            # Snapshot current state before mutating anything.
            cur.execute(
                """
                SELECT dr_cr, account_number, amount
                  FROM transaction_lines
                 WHERE transaction_id = %s
                 ORDER BY id
                """,
                (transaction_id,),
            )
            old_lines = cur.fetchall()
            snapshot = {
                "transaction_date": header["transaction_date"].isoformat(),
                "description": header["description"],
                "transaction_reference": header["transaction_reference"],
                "journal_type": header["journal_type"],
                "lines": [
                    {
                        "dr_cr": l["dr_cr"],
                        "account_number": l["account_number"],
                        "amount": str(l["amount"]),
                    }
                    for l in old_lines
                ],
            }
            cur.execute(
                """
                INSERT INTO transaction_edits
                    (transaction_id, edited_by, reason, before_snapshot)
                VALUES (%s, %s, %s, %s)
                """,
                (transaction_id, edited_by, (reason or None), Json(snapshot)),
            )

            cur.execute(
                """
                UPDATE transactions
                   SET transaction_date = %s,
                       description = %s,
                       transaction_reference = %s
                 WHERE id = %s
                """,
                (transaction_date, description, transaction_reference, transaction_id),
            )

            # Replace the lines wholesale: delete the old set, insert the new.
            cur.execute(
                "DELETE FROM transaction_lines WHERE transaction_id = %s",
                (transaction_id,),
            )
            for line in lines:
                cur.execute(
                    """
                    INSERT INTO transaction_lines
                        (transaction_id, dr_cr, account_number, amount)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        transaction_id,
                        line["dr_cr"],
                        line["account_number"],
                        line["amount"],
                    ),
                )
            # COMMIT here -> deferred balance trigger validates the new lines.
    except psycopg.errors.DatabaseError as exc:
        # Same dispatch as post_transaction: GL001 = period-lock trigger,
        # P0001 = balance trigger. Anything else propagates.
        if exc.sqlstate == "GL001":
            current_lock = get_current_lock_date() or transaction_date
            raise PeriodLockedError(transaction_date, current_lock, "edit") from exc
        if exc.sqlstate == "P0001":
            raise UnbalancedTransactionError(str(exc)) from exc
        raise
