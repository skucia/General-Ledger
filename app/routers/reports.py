"""
Reports routes.

  GET /reports                                  — landing menu (5 buttons)
  GET /reports/trial-balance                    — Trial Balance
  GET /reports/balance-sheet                    — Balance Sheet
  GET /reports/profit-and-loss                  — Profit & Loss
  GET /reports/journal-listing                  — Journal Listing
  GET /reports/chart-of-accounts                — Chart of Accounts
  GET /reports/account-detail/{account_number}  — JSON drill-down (shared)

All routes are accessible to logged-in users including view-only.

Forms submit via GET so URLs are bookmarkable, refreshing is safe, and
browser back/forward works.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_current_user
from app.services import accounts as accounts_service
from app.services import reports as reports_service
from app.templating import templates

router = APIRouter()


# --- Reports landing menu ---------------------------------------------------

@router.get("/reports")
def reports_menu(
    request: Request,
    user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse(
        "reports_menu.html",
        {"request": request, "user": user},
    )


# --- Trial Balance ---------------------------------------------------------

def _fmt_amt(value: Decimal, blank_zero: bool = False) -> str:
    """Render a Decimal as '12,345.67'. Returns '' for zero when blank_zero=True."""
    if blank_zero and value == 0:
        return ""
    return f"{value:,.2f}"


def _fmt_amt_paren(value: Decimal) -> str:
    """
    Balance Sheet convention: parentheses around negatives, two decimals,
    thousands separators. e.g. Decimal('-1234.56') -> '(1,234.56)'.
    Used for account rows, subtotals, and the Profit/(Loss) line.
    """
    if value < 0:
        return f"({-value:,.2f})"
    return f"{value:,.2f}"


@router.get("/reports/trial-balance")
def trial_balance(
    request: Request,
    user: dict = Depends(get_current_user),
    as_of: Optional[str] = None,  # ?as_of=YYYY-MM-DD
):
    today = date.today()
    company_name = reports_service.get_company_name()

    # No query string -> show the form with today's default; no report yet.
    if as_of is None:
        return templates.TemplateResponse(
            "report_trial_balance.html",
            {
                "request": request,
                "user": user,
                "company_name": company_name,
                "as_of": today.isoformat(),
                "as_of_parsed": None,
                "submitted": False,
                "errors": [],
                "rows": [],
                "total_dr_display": "0.00",
                "total_cr_display": "0.00",
                "balanced": True,
            },
        )

    # Parse the date; surface a friendly error if invalid.
    try:
        as_of_parsed = date.fromisoformat(as_of)
    except ValueError:
        return templates.TemplateResponse(
            "report_trial_balance.html",
            {
                "request": request,
                "user": user,
                "company_name": company_name,
                "as_of": as_of,
                "as_of_parsed": None,
                "submitted": False,
                "errors": ["As of date is invalid."],
                "rows": [],
                "total_dr_display": "0.00",
                "total_cr_display": "0.00",
                "balanced": True,
            },
            status_code=400,
        )

    # Run the report.
    raw_rows = reports_service.trial_balance(as_of_parsed)

    total_dr = sum((r["dr"] for r in raw_rows), Decimal("0.00"))
    total_cr = sum((r["cr"] for r in raw_rows), Decimal("0.00"))

    # Pre-format display strings so the template stays simple.
    rows = [
        {
            "account_number": r["account_number"],
            "account_name": r["account_name"],
            "account_type": r["account_type"],
            "dr_display": _fmt_amt(r["dr"], blank_zero=True),
            "cr_display": _fmt_amt(r["cr"], blank_zero=True),
        }
        for r in raw_rows
    ]

    return templates.TemplateResponse(
        "report_trial_balance.html",
        {
            "request": request,
            "user": user,
            "company_name": company_name,
            "as_of": as_of,
            "as_of_parsed": as_of_parsed,
            "submitted": True,
            "errors": [],
            "rows": rows,
            "total_dr_display": _fmt_amt(total_dr),
            "total_cr_display": _fmt_amt(total_cr),
            "balanced": total_dr == total_cr,
        },
    )


@router.get("/reports/account-detail/{account_number}")
def account_detail(
    account_number: str,
    request: Request,
    user: dict = Depends(get_current_user),
    # Preferred upper-bound param. If absent, falls back to `as_of` for
    # backward compat with anything still using the old TB/BS URL shape.
    to_date: str = "",
    # Optional lower bound — when present, the response covers the date
    # range; when absent, it covers everything up to to_date (legacy mode).
    from_date: str = "",
    # Legacy alias for to_date.
    as_of: str = "",
):
    """
    JSON endpoint for the per-account drill-down modal. Used by Trial
    Balance, Balance Sheet, and P&L. Two modes:

      Up-to mode (TB / BS): only `to_date` (or legacy `as_of`) given.
        Returns every line up to and including the date.
        Running balance is cumulative.

      Range mode (P&L): both `from_date` and `to_date` given.
        Returns only lines in [from_date, to_date].
        Running balance starts at 0 — it represents period-only movement.

    Errors:
      400 if dates are missing/unparseable or from_date > to_date
      404 if account_number is unknown
    """
    upper_str = to_date or as_of
    if not upper_str:
        raise HTTPException(
            status_code=400,
            detail="to_date (or as_of) query parameter required",
        )
    try:
        upper = date.fromisoformat(upper_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid to_date / as_of")

    lower: Optional[date] = None
    if from_date:
        try:
            lower = date.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date")
        if lower > upper:
            raise HTTPException(
                status_code=400,
                detail="from_date must be on or before to_date",
            )

    detail = reports_service.account_detail(account_number, upper, lower)
    if detail is None:
        raise HTTPException(status_code=404, detail="Account not found")

    acct = detail["account"]
    return {
        "account": {
            "number": acct["account_number"],
            "name": acct["account_name"],
            "type": acct["account_type"],
            "type_label": accounts_service.ACCOUNT_TYPE_LABELS[acct["account_type"]],
        },
        # Date range echo. from_* is null when in up-to mode.
        "to_date": upper.isoformat(),
        "to_dmy": upper.strftime("%d/%m/%Y"),
        "from_date": lower.isoformat() if lower else None,
        "from_dmy": lower.strftime("%d/%m/%Y") if lower else None,
        "lines": [
            {
                "transaction_id": l["transaction_id"],
                "transaction_reference": l["transaction_reference"],
                "date_dmy": l["date"].strftime("%d/%m/%Y"),
                "description": l["description"],
                # 0..N attachments; the JS renders a 📎 per file plus an
                # "+ Add" link (full users) until the cap is reached.
                "attachments": l["attachments"],
                "dr": _fmt_amt(l["dr"], blank_zero=True),
                "cr": _fmt_amt(l["cr"], blank_zero=True),
                "balance": _fmt_amt(l["balance"]),
            }
            for l in detail["lines"]
        ],
    }


# --- Profit & Loss ---------------------------------------------------------

def _format_pl_section(rows: list) -> list:
    """Pre-format display strings for one P&L section (Sales or Costs)."""
    return [
        {
            "account_number": r["account_number"],
            "account_name": r["account_name"],
            "balance_display": _fmt_amt_paren(r["balance"]),
        }
        for r in rows
    ]


@router.get("/reports/profit-and-loss")
def profit_and_loss(
    request: Request,
    user: dict = Depends(get_current_user),
    from_date: Optional[str] = None,  # ?from_date=YYYY-MM-DD
    to_date: Optional[str] = None,    # ?to_date=YYYY-MM-DD
):
    today = date.today()
    # Default From: earliest transaction in the ledger (so the pre-filled
    # range actually contains the company's history). Falls back to first
    # of current month if no transactions exist yet.
    earliest = reports_service.get_earliest_transaction_date()
    default_from = earliest if earliest else today.replace(day=1)
    default_to = today
    company_name = reports_service.get_company_name()

    base_ctx = {
        "request": request,
        "user": user,
        "company_name": company_name,
        "submitted": False,
        "errors": [],
        "data": None,
    }

    # No query string at all -> show form with defaults, no report.
    if from_date is None and to_date is None:
        return templates.TemplateResponse(
            "report_profit_loss.html",
            {
                **base_ctx,
                "from_date": default_from.isoformat(),
                "to_date": default_to.isoformat(),
                "from_date_parsed": None,
                "to_date_parsed": None,
            },
        )

    # User submitted — fall back to defaults for any blank field.
    fd_str = from_date if from_date else default_from.isoformat()
    td_str = to_date if to_date else default_to.isoformat()

    errors = []
    fd_parsed: Optional[date] = None
    td_parsed: Optional[date] = None

    try:
        fd_parsed = date.fromisoformat(fd_str)
    except ValueError:
        errors.append("From date is invalid.")
    try:
        td_parsed = date.fromisoformat(td_str)
    except ValueError:
        errors.append("To date is invalid.")
    if fd_parsed and td_parsed and fd_parsed > td_parsed:
        errors.append("From date must be on or before To date.")

    if errors:
        return templates.TemplateResponse(
            "report_profit_loss.html",
            {
                **base_ctx,
                "from_date": fd_str,
                "to_date": td_str,
                "from_date_parsed": fd_parsed,
                "to_date_parsed": td_parsed,
                "errors": errors,
            },
            status_code=400,
        )

    # Run the report — uses the SAME shared function as the Balance Sheet's
    # P/L row, just with a from_date so the result is for-the-period rather
    # than all-time.
    pl = reports_service.profit_loss_breakdown(to_date=td_parsed, from_date=fd_parsed)

    sales_rows = _format_pl_section(pl["sales"])
    costs_rows = _format_pl_section(pl["costs"])

    is_empty = (
        not sales_rows and not costs_rows and pl["profit_or_loss"] == 0
    )

    data = {
        "sales": sales_rows,
        "costs": costs_rows,
        "total_sales_display":     _fmt_amt_paren(pl["total_sales"]),
        "total_costs_display":     _fmt_amt_paren(pl["total_costs"]),
        "profit_or_loss":          pl["profit_or_loss"],
        "profit_or_loss_display":  _fmt_amt_paren(pl["profit_or_loss"]),
        "is_empty":                is_empty,
    }

    return templates.TemplateResponse(
        "report_profit_loss.html",
        {
            **base_ctx,
            "from_date": fd_str,
            "to_date": td_str,
            "from_date_parsed": fd_parsed,
            "to_date_parsed": td_parsed,
            "submitted": True,
            "data": data,
        },
    )


# --- Balance Sheet ---------------------------------------------------------

def _format_bs_section(rows: list) -> list:
    """Pre-format display strings for one Balance Sheet section."""
    return [
        {
            "account_number": r["account_number"],
            "account_name": r["account_name"],
            "balance_display": _fmt_amt_paren(r["balance"]),
        }
        for r in rows
    ]


@router.get("/reports/balance-sheet")
def balance_sheet(
    request: Request,
    user: dict = Depends(get_current_user),
    as_of: Optional[str] = None,
):
    today = date.today()
    company_name = reports_service.get_company_name()

    # Skeleton context for both no-params and bad-date paths so the template
    # can always read the same keys.
    base_ctx = {
        "request": request,
        "user": user,
        "company_name": company_name,
        "submitted": False,
        "errors": [],
        "data": None,
    }

    # No query string -> show the form with today's default; no report yet.
    if as_of is None:
        return templates.TemplateResponse(
            "report_balance_sheet.html",
            {**base_ctx, "as_of": today.isoformat(), "as_of_parsed": None},
        )

    # Parse the date.
    try:
        as_of_parsed = date.fromisoformat(as_of)
    except ValueError:
        return templates.TemplateResponse(
            "report_balance_sheet.html",
            {
                **base_ctx,
                "as_of": as_of,
                "as_of_parsed": None,
                "errors": ["As of date is invalid."],
            },
            status_code=400,
        )

    # Run the report.
    bs = reports_service.balance_sheet(as_of_parsed)

    # Pre-format display strings so the template stays presentation-only.
    # Total Liabilities displays in parens always (subtractive presentation: it
    # gets deducted from Total Assets to derive Net Assets). Account-level
    # liability rows still show as natural-sign positives.
    total_liab = bs["total_liabilities"]
    total_liab_subtractive = (
        f"({total_liab:,.2f})" if total_liab != 0 else "0.00"
    )
    data = {
        "assets": _format_bs_section(bs["assets"]),
        "liabilities": _format_bs_section(bs["liabilities"]),
        "equity": _format_bs_section(bs["equity"]),
        "total_assets_display":             _fmt_amt_paren(bs["total_assets"]),
        "total_liabilities_display":        total_liab_subtractive,
        "net_assets":                       bs["net_assets"],
        "net_assets_display":               _fmt_amt_paren(bs["net_assets"]),
        "total_equity_display":             _fmt_amt_paren(bs["total_equity"]),
        "profit_or_loss":                   bs["profit_or_loss"],
        "profit_or_loss_display":           _fmt_amt_paren(bs["profit_or_loss"]),
        "total_equity_with_pl_display":     _fmt_amt_paren(bs["total_equity_with_pl"]),
        "balanced":                         bs["balanced"],
        "is_empty":                         bs["is_empty"],
    }

    return templates.TemplateResponse(
        "report_balance_sheet.html",
        {
            **base_ctx,
            "as_of": as_of,
            "as_of_parsed": as_of_parsed,
            "submitted": True,
            "data": data,
        },
    )


# --- Journal Listing -------------------------------------------------------

def _format_journal_lines(lines: list) -> list:
    """Pre-format DR/CR display strings for one transaction's lines."""
    out = []
    for l in lines:
        out.append({
            "dr_cr": l["dr_cr"],
            "account_number": l["account_number"],
            "account_name": l["account_name"],
            "dr_display": _fmt_amt(l["amount"]) if l["dr_cr"] == "DR" else "",
            "cr_display": _fmt_amt(l["amount"]) if l["dr_cr"] == "CR" else "",
        })
    return out


@router.get("/reports/journal-listing")
def journal_listing(
    request: Request,
    user: dict = Depends(get_current_user),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    account_number: Optional[str] = None,
):
    today = date.today()
    default_from = today.replace(day=1)
    default_to = today
    company_name = reports_service.get_company_name()
    all_accounts = accounts_service.list_accounts()  # for the datalist

    base_ctx = {
        "request": request,
        "user": user,
        "company_name": company_name,
        "all_accounts": all_accounts,
        "submitted": False,
        "errors": [],
        "data": None,
        "filter_account": None,
    }

    # No query string at all -> show form with defaults, no report.
    if from_date is None and to_date is None and not account_number:
        return templates.TemplateResponse(
            "report_journal_listing.html",
            {
                **base_ctx,
                "from_date": default_from.isoformat(),
                "to_date": default_to.isoformat(),
                "from_date_parsed": None,
                "to_date_parsed": None,
                "filter_account_input": "",
            },
        )

    # User submitted — fall back to defaults for any blank field.
    fd_str = from_date if from_date else default_from.isoformat()
    td_str = to_date if to_date else default_to.isoformat()
    account_input = (account_number or "").strip()

    errors = []
    fd_parsed: Optional[date] = None
    td_parsed: Optional[date] = None

    try:
        fd_parsed = date.fromisoformat(fd_str)
    except ValueError:
        errors.append("From date is invalid.")
    try:
        td_parsed = date.fromisoformat(td_str)
    except ValueError:
        errors.append("To date is invalid.")
    if fd_parsed and td_parsed and fd_parsed > td_parsed:
        errors.append("From date must be on or before To date.")

    # Optional account filter — validate existence (case-insensitive).
    filter_account = None
    canonical_account: Optional[str] = None
    if account_input:
        acct = accounts_service.get_account(account_input)
        if acct is None:
            errors.append(f"Account '{account_input}' does not exist.")
        else:
            canonical_account = acct["account_number"]
            filter_account = {
                "account_number": acct["account_number"],
                "account_name": acct["account_name"],
            }

    if errors:
        return templates.TemplateResponse(
            "report_journal_listing.html",
            {
                **base_ctx,
                "from_date": fd_str,
                "to_date": td_str,
                "from_date_parsed": fd_parsed,
                "to_date_parsed": td_parsed,
                # Preserve the user's raw input so they can fix typos.
                "filter_account_input": account_input,
                "errors": errors,
            },
            status_code=400,
        )

    # Run the report.
    raw = reports_service.journal_listing(
        from_date=fd_parsed,
        to_date=td_parsed,
        account_number=canonical_account,
    )

    transactions = []
    for t in raw["transactions"]:
        transactions.append({
            "id": t["id"],
            "date": t["date"],
            "reference": t["reference"],
            "description": t["description"],
            "posted_by": t["posted_by"],
            "attachments": t["attachments"],
            "lines": _format_journal_lines(t["lines"]),
            "total_dr_display": _fmt_amt(t["total_dr"]),
            "total_cr_display": _fmt_amt(t["total_cr"]),
        })

    data = {
        "transactions": transactions,
        "summary": {
            "txn_count": raw["summary"]["txn_count"],
            "total_dr_display": _fmt_amt(raw["summary"]["total_dr"]),
            "total_cr_display": _fmt_amt(raw["summary"]["total_cr"]),
            "attachment_count": raw["summary"]["attachment_count"],
        },
    }

    return templates.TemplateResponse(
        "report_journal_listing.html",
        {
            **base_ctx,
            "from_date": fd_str,
            "to_date": td_str,
            "from_date_parsed": fd_parsed,
            "to_date_parsed": td_parsed,
            # When valid, show canonical account in the form; otherwise raw input.
            "filter_account_input": canonical_account or account_input,
            "filter_account": filter_account,
            "submitted": True,
            "data": data,
        },
    )


# --- Chart of Accounts -----------------------------------------------------

@router.get("/reports/chart-of-accounts")
def chart_of_accounts(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Master-data listing of the chart of accounts. Pure list — no balances,
    no date filter, no drill-down. Renders today's date in the header.
    Available to all logged-in users including view-only.
    """
    return templates.TemplateResponse(
        "report_chart_of_accounts.html",
        {
            "request": request,
            "user": user,
            "company_name": reports_service.get_company_name(),
            "today": date.today(),
            "data": reports_service.chart_of_accounts(),
        },
    )
