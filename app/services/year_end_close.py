"""
Year-End Close service.

Composes:
  1. profit_loss_breakdown(to_date=close_date, from_date=last_close+1) —
     determines which P&L accounts have non-zero natural balances in
     the period being closed.
  2. preview_close() — runs all pre-flight validation and computes the
     closing-journal lines (zero each P&L account; balance to 3100
     Retained Earnings).
  3. execute_close() — atomically posts the closing journal AND inserts
     the period_locks row in the SAME DB transaction. Both succeed or
     both roll back.

Atomicity matters: if the lock insert failed after the closing journal
had committed, we'd have a closing journal sitting in an open period
that could be edited or duplicated. The closing journal is inserted
FIRST (the period-lock trigger sees no lock yet, passes), then the
lock is inserted. Both commit together.

Period scoping (subsequent closes work):
  - Period start = (last_close_date + 1 day) if a prior close exists
                 = None (i.e. inception) for the first close
  - Period end = close_date
  - All checks ("no existing YEC", "no 3100 activity", "P&L activity
    exists", and the actual P&L breakdown that drives the journal) use
    this scoped window.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, TypedDict

import psycopg

from app.db import get_connection
from app.services import accounts as accounts_service
from app.services import period_locks as locks_service
from app.services import reports as reports_service
from app.services import transactions as txn_service


# Could come from a settings table later; baked-in for now per spec.
RETAINED_EARNINGS_ACCOUNT = "3100"


class YearEndClosePreFlightError(Exception):
    """Raised by validate / preview / execute when the close cannot proceed."""


class YearEndClosePreviewLine(TypedDict):
    dr_cr: str                  # 'DR' or 'CR'
    account_number: str
    account_name: str
    amount: Decimal
    is_balancing_entry: bool    # True for the 3100 line (visually distinct)


class YearEndClosePreview(TypedDict):
    close_date: date
    last_close_date: Optional[date]
    period_start: Optional[date]   # None for first close
    profit_or_loss: Decimal        # signed; positive = profit
    lines: List[YearEndClosePreviewLine]
    journal_description: str
    journal_reference: str


# --- Validation ------------------------------------------------------------

def _validate_pre_close(close_date: date) -> Optional[date]:
    """
    Run all read-only pre-flight checks. Returns the previous close date
    (or None if first close). Raises YearEndClosePreFlightError on any
    failure with a user-friendly message.
    """
    today = date.today()
    if close_date > today:
        raise YearEndClosePreFlightError(
            f"Close date cannot be in the future. The latest possible "
            f"close date is today ({today.strftime('%d/%m/%Y')})."
        )

    # 3100 must exist
    re_account = accounts_service.get_account(RETAINED_EARNINGS_ACCOUNT)
    if re_account is None:
        raise YearEndClosePreFlightError(
            f"Cannot proceed: account {RETAINED_EARNINGS_ACCOUNT} "
            f"(Retained Earnings) does not exist in the chart of accounts."
        )

    # No lock at or before close_date
    current_lock = locks_service.get_current_lock_date()
    if current_lock is not None and current_lock >= close_date:
        raise YearEndClosePreFlightError(
            f"A period lock already covers the close date. Current lock "
            f"is through {current_lock.strftime('%d/%m/%Y')}; the close "
            f"date {close_date.strftime('%d/%m/%Y')} is on or before that."
        )

    # Period bounds: (last_close + 1) to close_date
    period_start = (current_lock + timedelta(days=1)) if current_lock else None

    # No existing YEC journal in this period; no 3100 activity in this period.
    with get_connection() as conn, conn.cursor() as cur:
        # Closing-journal check
        if period_start is None:
            cur.execute(
                """
                SELECT 1 FROM transactions
                 WHERE journal_type = 'YEAR_END_CLOSE'
                   AND transaction_date <= %s
                 LIMIT 1
                """,
                (close_date,),
            )
        else:
            cur.execute(
                """
                SELECT 1 FROM transactions
                 WHERE journal_type = 'YEAR_END_CLOSE'
                   AND transaction_date >= %s
                   AND transaction_date <= %s
                 LIMIT 1
                """,
                (period_start, close_date),
            )
        if cur.fetchone():
            raise YearEndClosePreFlightError(
                f"A closing journal (YEAR_END_CLOSE) already exists in "
                f"this period. Cannot post a second one."
            )

        # 3100 activity check (any line touching 3100 in this period)
        if period_start is None:
            cur.execute(
                """
                SELECT 1 FROM transaction_lines tl
                  JOIN transactions t ON t.id = tl.transaction_id
                 WHERE UPPER(tl.account_number) = UPPER(%s)
                   AND t.transaction_date <= %s
                 LIMIT 1
                """,
                (RETAINED_EARNINGS_ACCOUNT, close_date),
            )
        else:
            cur.execute(
                """
                SELECT 1 FROM transaction_lines tl
                  JOIN transactions t ON t.id = tl.transaction_id
                 WHERE UPPER(tl.account_number) = UPPER(%s)
                   AND t.transaction_date >= %s
                   AND t.transaction_date <= %s
                 LIMIT 1
                """,
                (RETAINED_EARNINGS_ACCOUNT, period_start, close_date),
            )
        if cur.fetchone():
            raise YearEndClosePreFlightError(
                f"Account {RETAINED_EARNINGS_ACCOUNT} has activity in "
                f"this period — cannot post a year-end close on top of "
                f"existing retained-earnings movement."
            )

    return current_lock  # may be None


# --- Preview (validation + closing-journal computation) -------------------

def preview_close(close_date: date) -> YearEndClosePreview:
    """
    Validate + compute the closing-journal lines. Used both for the
    confirmation panel (to show the admin what will happen) and as the
    first half of execute_close (which then atomically posts).

    Raises YearEndClosePreFlightError on any pre-flight failure or if
    the period has no P&L activity to close.
    """
    last_close = _validate_pre_close(close_date)
    period_start = (last_close + timedelta(days=1)) if last_close else None

    pl = reports_service.profit_loss_breakdown(
        to_date=close_date,
        from_date=period_start,
    )

    if not pl["sales"] and not pl["costs"]:
        raise YearEndClosePreFlightError(
            "No P&L activity in this period — nothing to close."
        )

    lines: List[YearEndClosePreviewLine] = []

    # Revenue closing entries — flip the CR-natural balances.
    # nat > 0 (normal): close with DR. nat < 0 (rare — refunds exceed sales):
    # close with CR. Either way we drive the balance back to zero.
    for r in pl["sales"]:
        nat = r["balance"]
        if nat == 0:
            continue
        lines.append({
            "dr_cr": "DR" if nat > 0 else "CR",
            "account_number": r["account_number"],
            "account_name": r["account_name"],
            "amount": abs(nat),
            "is_balancing_entry": False,
        })

    # Cost closing entries — flip the DR-natural balances.
    for c in pl["costs"]:
        nat = c["balance"]
        if nat == 0:
            continue
        lines.append({
            "dr_cr": "CR" if nat > 0 else "DR",
            "account_number": c["account_number"],
            "account_name": c["account_name"],
            "amount": abs(nat),
            "is_balancing_entry": False,
        })

    # Balancing entry to 3100 Retained Earnings.
    # Skip when net P/L is exactly zero — the revenue and cost closing
    # entries already balance, and amount=0 would violate the line CHECK.
    pl_amount = pl["profit_or_loss"]
    if pl_amount != 0:
        re_account = accounts_service.get_account(RETAINED_EARNINGS_ACCOUNT)
        lines.append({
            "dr_cr": "CR" if pl_amount > 0 else "DR",
            "account_number": re_account["account_number"],
            "account_name": re_account["account_name"],
            "amount": abs(pl_amount),
            "is_balancing_entry": True,
        })

    return {
        "close_date": close_date,
        "last_close_date": last_close,
        "period_start": period_start,
        "profit_or_loss": pl_amount,
        "lines": lines,
        "journal_description": (
            f"Year-end close for period ending {close_date.strftime('%d/%m/%Y')}"
        ),
        "journal_reference": f"YEC-{close_date.year}",
    }


# --- Execute (atomic) -----------------------------------------------------

def execute_close(close_date: date, admin_id: int) -> dict:
    """
    Atomically post the closing journal AND insert the period lock.
    Re-runs all pre-flight checks (defense against race conditions) by
    calling preview_close().

    Returns:
      {
        "txn_id": int,                # id of the closing-journal transaction
        "close_date": date,
        "lines_posted": int,
        "profit_or_loss": Decimal,    # signed
      }

    Raises:
      YearEndClosePreFlightError — any validation failure, including the
        rare race-condition case where DR=CR fails (which would mean a
        bug in the closing-journal computation).
    """
    preview = preview_close(close_date)  # re-validates + recomputes

    # Closing journal lines for insert_transaction_within (drop the
    # presentation fields — account_name and is_balancing_entry).
    journal_lines = [
        {
            "dr_cr": l["dr_cr"],
            "account_number": l["account_number"],
            "amount": l["amount"],
        }
        for l in preview["lines"]
    ]

    try:
        with get_connection() as conn, conn.cursor() as cur:
            # Defensive chokepoint check — guards against a lock added
            # by another session between pre-flight (above) and now.
            txn_service.assert_period_open(close_date, operation="post")

            # 1. Post the closing journal. The period-lock trigger fires
            #    per-INSERT and sees NO lock for close_date yet (we
            #    insert it second), so passes.
            txn_id = txn_service.insert_transaction_within(
                cur,
                transaction_date=close_date,
                description=preview["journal_description"],
                transaction_reference=preview["journal_reference"],
                created_by=admin_id,
                lines=journal_lines,
                reverses_transaction_id=None,
                journal_type="YEAR_END_CLOSE",
            )

            # 2. Insert the period lock in the SAME transaction. After
            #    COMMIT the trigger will see this row and reject any
            #    further writes to closed-period transactions.
            cur.execute(
                """
                INSERT INTO period_locks (locked_through, locked_by, reason)
                VALUES (%s, %s, %s)
                """,
                (close_date, admin_id, f"Year-end close {close_date.year}"),
            )
            # COMMIT here. The DR=CR balance trigger fires (deferred);
            # closing journal balances by construction so it passes.
    except psycopg.errors.DatabaseError as exc:
        # Connection has rolled back automatically. Two interesting cases:
        #   - 'P0001' -> DR=CR balance check failed (would be a bug)
        #   - 'GL001' -> race-added lock (caught by chokepoint above
        #                normally; here as a final backstop)
        if exc.sqlstate == "P0001":
            raise YearEndClosePreFlightError(
                f"Closing journal didn't balance — this should not "
                f"happen. Database error: {exc}"
            ) from exc
        if exc.sqlstate == "GL001":
            raise YearEndClosePreFlightError(
                f"A period lock was added by another session during "
                f"the close. Please retry. Database error: {exc}"
            ) from exc
        raise

    return {
        "txn_id": txn_id,
        "close_date": close_date,
        "lines_posted": len(journal_lines),
        "profit_or_loss": preview["profit_or_loss"],
    }
