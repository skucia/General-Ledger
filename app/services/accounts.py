"""
Database queries for the accounts table (chart of accounts).

Account numbers are treated as case-insensitive: the app uppercases
every account_number before insert and looks them up with UPPER(...) in
SQL, so 'abc123' and 'ABC123' refer to the same account.

The DB-level CHECK constraint still allows mixed case (it was set up in
Phase 1 as `^[A-Za-z0-9]+$`). We rely on the application as the only
write path; if we ever open up direct DB inserts we should tighten the
constraint to `^[A-Z0-9]+$` via a new migration.
"""

from typing import List, Optional, Tuple

from psycopg.rows import dict_row

from app.db import get_connection


# --- Account-type metadata -------------------------------------------------
# Accounting display order: Assets, Liabilities, Equity, Sales, Costs.
# Used by both the list-grouping function below and the type dropdown
# on the Add Account form.
ACCOUNT_TYPE_ORDER: List[str] = ["A", "L", "E", "S", "C"]

ACCOUNT_TYPE_LABELS: dict = {
    "A": "Asset",
    "L": "Liability",
    "E": "Equity",
    "S": "Sales",
    "C": "Costs",
}


class AccountNumberTakenError(Exception):
    """Raised by create_account when the chosen number already exists (case-insensitive)."""

    def __init__(self, account_number: str):
        super().__init__(f"account number '{account_number}' already exists")
        self.account_number = account_number


# --- Queries ---------------------------------------------------------------

def get_account(account_number: str) -> Optional[dict]:
    """Look up one account by number (case-insensitive). Used by Phase 6 validation."""
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT account_number, account_name, account_type, created_at
              FROM accounts
             WHERE UPPER(account_number) = UPPER(%s)
            """,
            (account_number,),
        )
        return cur.fetchone()


def list_accounts() -> List[dict]:
    """
    Returns every account, sorted by accounting type (A, L, E, S, C) and
    then by account_number ascending within each type.
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT account_number, account_name, account_type, created_at
              FROM accounts
             ORDER BY CASE account_type
                        WHEN 'A' THEN 1
                        WHEN 'L' THEN 2
                        WHEN 'E' THEN 3
                        WHEN 'S' THEN 4
                        WHEN 'C' THEN 5
                      END,
                      account_number
            """
        )
        return cur.fetchall()


def group_accounts_by_type(accounts: List[dict]) -> List[Tuple[str, str, List[dict]]]:
    """
    Convert a flat list of accounts into [(type_code, type_label, [accounts]), ...]
    in accounting order. Groups with no accounts are omitted.
    """
    by_type: dict = {}
    for acc in accounts:
        by_type.setdefault(acc["account_type"], []).append(acc)
    return [
        (code, ACCOUNT_TYPE_LABELS[code], by_type[code])
        for code in ACCOUNT_TYPE_ORDER
        if code in by_type
    ]


def create_account(
    account_number: str,
    account_name: str,
    account_type: str,
) -> str:
    """
    Insert a new account. Account number is uppercased and uniqueness is
    enforced case-insensitively. Returns the (uppercased) account_number.

    Raises:
        AccountNumberTakenError: if the (case-insensitive) number already exists.
    """
    # Belt and suspenders: uppercase here too, in case a caller forgot.
    account_number = account_number.upper()

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM accounts WHERE UPPER(account_number) = %s",
            (account_number,),
        )
        if cur.fetchone():
            raise AccountNumberTakenError(account_number)

        cur.execute(
            """
            INSERT INTO accounts (account_number, account_name, account_type)
            VALUES (%s, %s, %s)
            """,
            (account_number, account_name, account_type),
        )
        return account_number
