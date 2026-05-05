"""
Report queries.

Every function here returns plain Python objects (lists/dicts of Decimal,
date, str) — formatting for display happens in the route or template layer.
This keeps the SQL focused and makes it easy to reuse the same data for
HTML, JSON, and (later) CSV/PDF exports.

All queries use parameterised placeholders (%s) — no string concatenation
of user input.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional, TypedDict

from psycopg.rows import dict_row

from app.db import get_connection
from app.services import accounts as accounts_service


# --- Company settings ------------------------------------------------------

def get_company_name() -> str:
    """Returns the singleton company name from company_settings."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT company_name FROM company_settings WHERE id = 1")
        row = cur.fetchone()
        return row[0] if row else ""


# --- Shared building block -------------------------------------------------

def _account_balances(
    to_date: date,
    from_date: Optional[date] = None,
) -> List[dict]:
    """
    Internal helper. Returns one row per account in the chart with raw
    `dr_sum` and `cr_sum` for transactions in the period.

      - to_date alone   -> "all time up to to_date" (Trial Balance, Balance Sheet)
      - from_date+to_date -> "between from_date and to_date inclusive" (P&L)

    Accounts with no activity get 0/0. Zero-balance accounts are NOT
    filtered — every caller (Trial Balance / Balance Sheet / P&L)
    decides what to do with them.
    """
    if from_date is None:
        where_clause = "t.transaction_date <= %s"
        params: list = [to_date]
    else:
        where_clause = "t.transaction_date >= %s AND t.transaction_date <= %s"
        params = [from_date, to_date]

    sql = f"""
        WITH balances AS (
            SELECT tl.account_number,
                   SUM(CASE WHEN tl.dr_cr = 'DR' THEN tl.amount ELSE 0 END) AS dr_sum,
                   SUM(CASE WHEN tl.dr_cr = 'CR' THEN tl.amount ELSE 0 END) AS cr_sum
              FROM transaction_lines tl
              JOIN transactions t ON t.id = tl.transaction_id
             WHERE {where_clause}
             GROUP BY tl.account_number
        )
        SELECT a.account_number,
               a.account_name,
               a.account_type,
               COALESCE(b.dr_sum, 0)::numeric(18,2) AS dr_sum,
               COALESCE(b.cr_sum, 0)::numeric(18,2) AS cr_sum
          FROM accounts a
          LEFT JOIN balances b ON b.account_number = a.account_number
         ORDER BY a.account_number
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# --- Trial Balance ---------------------------------------------------------

class TrialBalanceRow(TypedDict):
    account_number: str
    account_name: str
    account_type: str
    dr: Decimal   # 0 if the account's net side is CR
    cr: Decimal   # 0 if the account's net side is DR


def trial_balance(as_of: date) -> List[TrialBalanceRow]:
    """
    Trial Balance presentation. For each account, compute net = DR - CR.
    Positive nets land in the DR column; negative nets land (as magnitude)
    in the CR column. Zero-balance accounts are hidden.
    """
    rows = _account_balances(as_of)
    out: List[TrialBalanceRow] = []
    for r in rows:
        net = r["dr_sum"] - r["cr_sum"]
        if net == 0:
            continue
        out.append({
            "account_number": r["account_number"],
            "account_name": r["account_name"],
            "account_type": r["account_type"],
            "dr": net if net > 0 else Decimal("0.00"),
            "cr": -net if net < 0 else Decimal("0.00"),
        })
    return out


# --- Profit / (Loss) — SOURCE OF TRUTH for both Balance Sheet and P&L -----
#
# DO NOT re-derive this calculation anywhere else. The Balance Sheet
# consumes only `profit_or_loss`; the (future) P&L report consumes the full
# breakdown. Keeping a single function guarantees the two reports can never
# disagree.

def _profit_loss_from_rows(rows: List[dict]) -> dict:
    """Pure function: derive the P&L breakdown from already-fetched _account_balances rows."""
    sales: List[dict] = []
    costs: List[dict] = []
    for r in rows:
        if r["account_type"] == "S":
            # Sales is CR-natural — natural balance = ΣCR − ΣDR
            bal = r["cr_sum"] - r["dr_sum"]
            if bal != 0:
                sales.append({
                    "account_number": r["account_number"],
                    "account_name": r["account_name"],
                    "balance": bal,
                })
        elif r["account_type"] == "C":
            # Costs is DR-natural — natural balance = ΣDR − ΣCR
            bal = r["dr_sum"] - r["cr_sum"]
            if bal != 0:
                costs.append({
                    "account_number": r["account_number"],
                    "account_name": r["account_name"],
                    "balance": bal,
                })

    total_sales = sum((s["balance"] for s in sales), Decimal("0.00"))
    total_costs = sum((c["balance"] for c in costs), Decimal("0.00"))
    return {
        "sales": sales,
        "costs": costs,
        "total_sales": total_sales,
        "total_costs": total_costs,
        "profit_or_loss": total_sales - total_costs,
    }


# --- Chart of Accounts -----------------------------------------------------

def chart_of_accounts() -> dict:
    """
    Master-data listing of the chart of accounts. No balances, no date
    filtering — just every account in the chart, grouped by type in
    accounting order (A, L, E, S, C).

    Unlike accounts_service.group_accounts_by_type() (which is used by the
    Add Accounts screen and drops empty groups for compactness), this
    function ALWAYS returns one entry per type code, even when a group
    has no accounts — the Chart of Accounts report needs to surface
    "(no accounts in this group)" placeholders.

    Returns:
      {
        "groups": [
          {"type_code": "A", "type_label": "Asset",
           "accounts": [{"account_number","account_name","account_type"}, ...],
           "count": int},
          ...   # always 5 entries, in accounting order
        ],
        "total_accounts": int,
        "type_count": int,    # always 5; drives the "across N types" footer copy
      }
    """
    rows = accounts_service.list_accounts()  # already sorted by type then number

    by_type: dict = {}
    for r in rows:
        by_type.setdefault(r["account_type"], []).append(r)

    groups = []
    for code in accounts_service.ACCOUNT_TYPE_ORDER:
        accounts = by_type.get(code, [])
        groups.append({
            "type_code": code,
            "type_label": accounts_service.ACCOUNT_TYPE_LABELS[code],
            "accounts": accounts,
            "count": len(accounts),
        })

    return {
        "groups": groups,
        "total_accounts": len(rows),
        "type_count": len(accounts_service.ACCOUNT_TYPE_ORDER),
    }


# --- Journal Listing -------------------------------------------------------

def journal_listing(
    from_date: date,
    to_date: date,
    account_number: Optional[str] = None,
) -> dict:
    """
    Transaction-centric listing for the date range.

    If `account_number` is provided (already validated + canonicalised by the
    caller), only include transactions that have at least one line touching
    that account — but each transaction still includes ALL its lines, so the
    caller sees the complete journal entry, not just the matching lines.

    Returns:
      {
        "transactions": [
          {
            "id": int,
            "date": date,
            "reference": str,
            "description": str,
            "posted_by": str,
            "attachment_filename": str | None,   # original filename, None if no attachment
            "lines": [{dr_cr, account_number, account_name, amount}, ...],
            "total_dr": Decimal,
            "total_cr": Decimal,
          },
          ...
        ],
        "summary": {
          "txn_count": int,
          "total_dr": Decimal,
          "total_cr": Decimal,
          "attachment_count": int,
        },
      }
    """
    # Build header query — optionally narrowed to transactions touching the
    # filter account via EXISTS. Parameterised either way.
    header_params: list = [from_date, to_date]
    account_filter_sql = ""
    if account_number:
        account_filter_sql = """
            AND EXISTS (
                SELECT 1 FROM transaction_lines tl
                 WHERE tl.transaction_id = t.id
                   AND UPPER(tl.account_number) = UPPER(%s)
            )
        """
        header_params.append(account_number)

    header_sql = f"""
        SELECT t.id,
               t.transaction_date,
               t.transaction_reference,
               t.description,
               t.attachment_path,
               t.attachment_original_name,
               u.username AS posted_by
          FROM transactions t
          JOIN users u ON u.id = t.created_by
         WHERE t.transaction_date >= %s
           AND t.transaction_date <= %s
           {account_filter_sql}
         ORDER BY t.transaction_date ASC, t.id ASC
    """

    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(header_sql, header_params)
        headers = cur.fetchall()

        if not headers:
            return {
                "transactions": [],
                "summary": {
                    "txn_count": 0,
                    "total_dr": Decimal("0.00"),
                    "total_cr": Decimal("0.00"),
                    "attachment_count": 0,
                },
            }

        txn_ids = [h["id"] for h in headers]
        cur.execute(
            """
            SELECT tl.transaction_id,
                   tl.id AS line_id,
                   tl.dr_cr,
                   tl.account_number,
                   tl.amount,
                   a.account_name
              FROM transaction_lines tl
              JOIN accounts a ON a.account_number = tl.account_number
             WHERE tl.transaction_id = ANY(%s)
             ORDER BY tl.transaction_id, tl.id
            """,
            (txn_ids,),
        )
        all_lines = cur.fetchall()

    # Group lines by transaction_id, then attach to headers in order.
    lines_by_txn: dict = {}
    for line in all_lines:
        lines_by_txn.setdefault(line["transaction_id"], []).append(line)

    transactions = []
    grand_dr = Decimal("0.00")
    grand_cr = Decimal("0.00")
    attachment_count = 0

    for h in headers:
        txn_lines = lines_by_txn.get(h["id"], [])
        block_dr = sum(
            (l["amount"] for l in txn_lines if l["dr_cr"] == "DR"),
            Decimal("0.00"),
        )
        block_cr = sum(
            (l["amount"] for l in txn_lines if l["dr_cr"] == "CR"),
            Decimal("0.00"),
        )
        grand_dr += block_dr
        grand_cr += block_cr
        if h["attachment_path"]:
            attachment_count += 1

        transactions.append({
            "id": h["id"],
            "date": h["transaction_date"],
            "reference": h["transaction_reference"],
            "description": h["description"],
            "posted_by": h["posted_by"],
            "attachment_filename": h["attachment_original_name"],
            "lines": [
                {
                    "dr_cr": l["dr_cr"],
                    "account_number": l["account_number"],
                    "account_name": l["account_name"],
                    "amount": l["amount"],
                }
                for l in txn_lines
            ],
            "total_dr": block_dr,
            "total_cr": block_cr,
        })

    return {
        "transactions": transactions,
        "summary": {
            "txn_count": len(transactions),
            "total_dr": grand_dr,
            "total_cr": grand_cr,
            "attachment_count": attachment_count,
        },
    }


def profit_loss_breakdown(
    to_date: date,
    from_date: Optional[date] = None,
) -> dict:
    """
    Public API for the P&L calculation. Returns a dict with `sales`,
    `costs`, `total_sales`, `total_costs`, and `profit_or_loss`.

      - to_date alone -> "P/L from start of time up to to_date" (used by
        the Balance Sheet's P/L row, indirectly via balance_sheet()).
      - from_date+to_date -> "P/L for the date range" (used by the P&L
        report).

    See _profit_loss_from_rows for the actual calc.
    """
    return _profit_loss_from_rows(_account_balances(to_date, from_date))


# --- Balance Sheet ---------------------------------------------------------

# DR-natural account types used by the natural-balance computation in
# both Balance Sheet sections and the drill-down running balance.
_DR_NATURAL_BS_TYPES = {"A"}      # only Assets in the BS body (Costs is in P/L)
_CR_NATURAL_BS_TYPES = {"L", "E"} # Liabilities and Equity in the BS body


def balance_sheet(as_of: date) -> dict:
    """
    Balance Sheet presentation:
      {
        "assets":               [{account_number, account_name, balance}, ...],
        "liabilities":          [...],
        "equity":               [...],
        "total_assets":         Decimal,
        "total_liabilities":    Decimal,
        "total_equity":         Decimal,
        "profit_or_loss":       Decimal,   # from profit_loss_breakdown
        "total_equity_with_pl": Decimal,
        "total_liab_eq_pl":     Decimal,
        "balanced":             bool,
        "is_empty":             bool,      # True only when no A/L/E rows AND P/L == 0
      }

    All `balance` fields are NATURAL SIGN for the account type:
      - Assets (DR-natural):       balance = ΣDR − ΣCR
      - Liabilities/Equity (CR-natural): balance = ΣCR − ΣDR
    Zero-balance accounts in A/L/E are filtered out.
    """
    rows = _account_balances(as_of)

    assets: List[dict] = []
    liabilities: List[dict] = []
    equity: List[dict] = []

    for r in rows:
        t = r["account_type"]
        if t in _DR_NATURAL_BS_TYPES:
            bal = r["dr_sum"] - r["cr_sum"]
        elif t in _CR_NATURAL_BS_TYPES:
            bal = r["cr_sum"] - r["dr_sum"]
        else:
            continue  # S and C contribute to P/L only, not to BS sections

        if bal == 0:
            continue

        entry = {
            "account_number": r["account_number"],
            "account_name": r["account_name"],
            "balance": bal,
        }
        if t == "A":
            assets.append(entry)
        elif t == "L":
            liabilities.append(entry)
        elif t == "E":
            equity.append(entry)

    total_assets = sum((a["balance"] for a in assets), Decimal("0.00"))
    total_liabilities = sum((l["balance"] for l in liabilities), Decimal("0.00"))
    total_equity = sum((e["balance"] for e in equity), Decimal("0.00"))

    # Reuse the SAME rows we just fetched — no second DB query.
    pl_breakdown = _profit_loss_from_rows(rows)
    profit_or_loss = pl_breakdown["profit_or_loss"]

    total_equity_with_pl = total_equity + profit_or_loss
    total_liab_eq_pl = total_liabilities + total_equity_with_pl

    return {
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "profit_or_loss": profit_or_loss,
        "total_equity_with_pl": total_equity_with_pl,
        "total_liab_eq_pl": total_liab_eq_pl,
        "balanced": total_assets == total_liab_eq_pl,
        "is_empty": (
            not assets and not liabilities and not equity and profit_or_loss == 0
        ),
    }


# --- Trial Balance: per-account drill-down --------------------------------

class AccountDetailLine(TypedDict):
    transaction_id: int
    transaction_reference: str       # may be '' for legacy rows pre-migration 005
    date: date
    description: str
    attachment_filename: Optional[str]   # original filename, or None if no attachment
    dr: Decimal                      # 0 if this line is a CR
    cr: Decimal                      # 0 if this line is a DR
    balance: Decimal                 # running balance (Option B natural sign)


class AccountDetail(TypedDict):
    account: dict
    lines: List[AccountDetailLine]


# DR-natural account types: Asset and Cost. Their running balance accumulates DR - CR.
# All other types (L, E, S) are CR-natural — their running balance accumulates CR - DR.
_DR_NATURAL_TYPES = {"A", "C"}


def account_detail(
    account_number: str,
    to_date: date,
    from_date: Optional[date] = None,
) -> Optional[AccountDetail]:
    """
    Fetch every transaction line for one account in the period, in
    chronological order (date asc, then transaction id asc, then line id
    asc as a final tiebreaker for stable ordering).

      - to_date alone:    all lines up to and including to_date
      - with from_date:   only lines where from_date <= date <= to_date

    Computes a running balance using Option B (natural sign per account type):
      - A, C  -> balance accumulates DR − CR
      - L, E, S -> balance accumulates CR − DR

    For range mode, the running balance starts at 0 for the first in-range
    line — it represents period-only movement (matches what the P&L report
    itself shows). For up-to mode, the running balance is cumulative
    (matches Trial Balance / Balance Sheet).

    Returns None if the account doesn't exist.
    """
    account = accounts_service.get_account(account_number)
    if account is None:
        return None

    if from_date is None:
        where_dates = "t.transaction_date <= %s"
        date_params: list = [to_date]
    else:
        where_dates = "t.transaction_date >= %s AND t.transaction_date <= %s"
        date_params = [from_date, to_date]

    sql = f"""
        SELECT t.id                       AS transaction_id,
               t.transaction_date,
               t.transaction_reference,
               t.description,
               t.attachment_original_name,
               tl.id                      AS line_id,
               tl.dr_cr,
               tl.amount
          FROM transaction_lines tl
          JOIN transactions t ON t.id = tl.transaction_id
         WHERE tl.account_number = %s
           AND {where_dates}
         ORDER BY t.transaction_date ASC, t.id ASC, tl.id ASC
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, [account["account_number"], *date_params])
        rows = cur.fetchall()

    is_dr_natural = account["account_type"] in _DR_NATURAL_TYPES
    balance = Decimal("0.00")
    lines: List[AccountDetailLine] = []
    for r in rows:
        dr_amt = r["amount"] if r["dr_cr"] == "DR" else Decimal("0.00")
        cr_amt = r["amount"] if r["dr_cr"] == "CR" else Decimal("0.00")
        if is_dr_natural:
            balance = balance + dr_amt - cr_amt
        else:
            balance = balance + cr_amt - dr_amt
        lines.append({
            "transaction_id": r["transaction_id"],
            "transaction_reference": r["transaction_reference"] or "",
            "date": r["transaction_date"],
            "description": r["description"],
            "attachment_filename": r["attachment_original_name"],
            "dr": dr_amt,
            "cr": cr_amt,
            "balance": balance,
        })

    return {"account": account, "lines": lines}
