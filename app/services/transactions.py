"""
Transaction-posting service.

post_transaction() is the only write path. It runs all INSERTs (header +
lines) inside a single connection transaction. Our DR=CR balance trigger
is DEFERRABLE INITIALLY DEFERRED, so it doesn't fire until COMMIT — which
means the app can insert the header and N lines and the trigger sees the
final, complete picture once.

If something goes wrong (the trigger raises, an FK fails, anything else),
the connection helper rolls back automatically, so partial transactions
can never end up in the DB.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional, TypedDict

import psycopg

from app.db import get_connection


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


def post_transaction(
    transaction_date: date,
    description: str,
    transaction_reference: str,
    attachment_filename: Optional[str],
    attachment_original_name: Optional[str],
    created_by: int,
    lines: List[TransactionLineInput],
) -> int:
    """
    Insert one transaction header + its lines atomically. Returns the new
    transaction id. Raises UnbalancedTransactionError if the DB trigger
    objects to the totals at COMMIT time.
    """
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions (
                    transaction_date,
                    description,
                    transaction_reference,
                    attachment_path,
                    attachment_original_name,
                    created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    transaction_date,
                    description,
                    transaction_reference,
                    attachment_filename,
                    attachment_original_name,
                    created_by,
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
            # COMMIT happens here as the context manager exits. The deferred
            # constraint trigger fires at COMMIT — if the totals don't match,
            # Postgres raises and our context manager rolls back.
        return txn_id
    except psycopg.errors.RaiseException as exc:
        # Translate the trigger's RAISE EXCEPTION into our typed error.
        raise UnbalancedTransactionError(str(exc)) from exc
