"""
Transaction-entry routes.

  GET  /transactions/new   — empty form with two starting line rows
  POST /transactions/new   — validate, post atomically, redirect on success

Validation order (errors short-circuit per-line where appropriate):
  1. Date parses as YYYY-MM-DD.
  2. At least 2 lines submitted.
  3. Each line: DR/CR ∈ {DR, CR}; account exists; amount is a positive Decimal.
  4. Σ DR == Σ CR (compared as Decimals).
  5. Attachment (if any): size ≤ 10 MB.
  6. Insert (header + lines) inside one DB transaction; deferred trigger is
     the fail-safe.

On error: re-render the form with the user's submitted values preserved so
they don't lose multi-row work. On success: redirect back to /transactions/new
with a green flash banner (per Phase 6 decision — practical for posting many
transactions in a row).
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app.auth import get_current_user, require_full_user
from app.config import settings
from app.services import accounts as accounts_service
from app.services import transactions as transactions_service
from app.templating import flash, templates

router = APIRouter()

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


def _empty_lines(n: int = 2) -> List[dict]:
    """Two blank line rows for the initial GET render."""
    return [{"dr_cr": "", "account_number": "", "amount": ""} for _ in range(n)]


def _render_form(
    request: Request,
    user: dict,
    transaction_date: str,
    description: str,
    transaction_reference: str,
    lines: List[dict],
    errors: List[str],
    status_code: int = 200,
):
    """Render add_transaction.html with the given state. Used by both GET and the error path of POST."""
    return templates.TemplateResponse(
        "add_transaction.html",
        {
            "request": request,
            "user": user,
            "form": {
                "transaction_date": transaction_date,
                "description": description,
                "transaction_reference": transaction_reference,
                "lines": lines,
            },
            "accounts": accounts_service.list_accounts(),
            "errors": errors,
        },
        status_code=status_code,
    )


@router.get("/transactions/new")
def add_transaction_form(
    request: Request,
    user: dict = Depends(get_current_user),
):
    return _render_form(
        request=request,
        user=user,
        transaction_date=date.today().isoformat(),
        description="",
        transaction_reference="",
        lines=_empty_lines(2),
        errors=[],
    )


@router.post("/transactions/new")
async def add_transaction_submit(
    request: Request,
    # require_full_user blocks view-only users at the door.
    user: dict = Depends(require_full_user),
    transaction_date: str = Form(...),
    # Use Form(default="") for free-text fields that we validate ourselves so
    # an empty submission lands in our handler (with friendly errors) rather
    # than being rejected at FastAPI's parsing layer with a generic 422.
    description: str = Form(default=""),
    transaction_reference: str = Form(default=""),
    # The three line fields submit as parallel arrays — same length, same order.
    dr_cr: List[str] = Form(default=[]),
    account_number: List[str] = Form(default=[]),
    amount: List[str] = Form(default=[]),
    attachment: Optional[UploadFile] = File(None),
):
    description = description.strip()
    transaction_reference = transaction_reference.strip()
    # Re-zip into per-line dicts so we can render them back on error.
    raw_lines = [
        {"dr_cr": d, "account_number": a, "amount": m}
        for d, a, m in zip(dr_cr, account_number, amount)
    ]

    errors: List[str] = []
    parsed_lines: List[transactions_service.TransactionLineInput] = []

    # --- 1. Date ----------------------------------------------------------
    try:
        txn_date = date.fromisoformat(transaction_date)
    except ValueError:
        errors.append("Transaction date is invalid.")
        txn_date = None  # type: ignore

    # --- 2. Description ---------------------------------------------------
    if not description:
        errors.append("Description is required.")
    elif len(description) > 200:
        errors.append("Description must be 200 characters or fewer.")

    # --- 2b. Reference ----------------------------------------------------
    if not transaction_reference:
        errors.append("Reference is required.")
    elif len(transaction_reference) > 20:
        errors.append("Reference must be 20 characters or fewer.")

    # --- 3. Line count ----------------------------------------------------
    if len(raw_lines) < 2:
        errors.append("At least 2 lines are required (one DR + one CR).")

    # --- 3. Per-line validation ------------------------------------------
    for i, line in enumerate(raw_lines, start=1):
        line_dr = (line["dr_cr"] or "").strip().upper()
        line_acc = (line["account_number"] or "").strip()
        line_amt = (line["amount"] or "").strip()

        if line_dr not in ("DR", "CR"):
            errors.append(f"Line {i}: choose DR or CR.")
            continue
        if not line_acc:
            errors.append(f"Line {i}: account number is required.")
            continue

        acct = accounts_service.get_account(line_acc)
        if acct is None:
            errors.append(f"Line {i}: account '{line_acc}' does not exist.")
            continue

        try:
            amt_dec = Decimal(line_amt)
        except (InvalidOperation, ValueError):
            errors.append(f"Line {i}: amount must be a number.")
            continue

        if amt_dec <= 0:
            errors.append(f"Line {i}: amount must be greater than zero.")
            continue

        parsed_lines.append({
            "dr_cr": line_dr,
            "account_number": acct["account_number"],  # canonical uppercase
            "amount": amt_dec.quantize(Decimal("0.01")),
        })

    # --- 4. DR=CR check (only if every line parsed cleanly) --------------
    dr_total = Decimal("0.00")
    cr_total = Decimal("0.00")
    if not errors and len(parsed_lines) >= 2:
        dr_total = sum(
            (l["amount"] for l in parsed_lines if l["dr_cr"] == "DR"),
            Decimal("0.00"),
        )
        cr_total = sum(
            (l["amount"] for l in parsed_lines if l["dr_cr"] == "CR"),
            Decimal("0.00"),
        )
        if dr_total != cr_total:
            errors.append(
                f"Transaction is not balanced: "
                f"DR=${dr_total:,.2f}, CR=${cr_total:,.2f}, "
                f"Difference=${(dr_total - cr_total):,.2f}. "
                f"Please correct and resubmit."
            )

    # --- 5. Attachment ----------------------------------------------------
    saved_filename: Optional[str] = None
    original_name: Optional[str] = None
    attachment_bytes: Optional[bytes] = None
    if attachment is not None and attachment.filename:
        attachment_bytes = await attachment.read()
        if len(attachment_bytes) > MAX_ATTACHMENT_BYTES:
            errors.append("Attachment is larger than 10 MB.")
            attachment_bytes = None
        else:
            ext = Path(attachment.filename).suffix
            saved_filename = f"{uuid4().hex}{ext}"
            original_name = attachment.filename

    # --- Bail on validation errors ---------------------------------------
    if errors:
        return _render_form(
            request=request,
            user=user,
            transaction_date=transaction_date,
            description=description,
            transaction_reference=transaction_reference,
            lines=raw_lines,
            errors=errors,
            status_code=400,
        )

    # --- Save attachment, then post the transaction atomically -----------
    if saved_filename and attachment_bytes is not None:
        save_path = settings.upload_dir / saved_filename
        save_path.write_bytes(attachment_bytes)

    try:
        txn_id = transactions_service.post_transaction(
            transaction_date=txn_date,
            description=description,
            transaction_reference=transaction_reference,
            attachment_filename=saved_filename,
            attachment_original_name=original_name,
            created_by=user["id"],
            lines=parsed_lines,
        )
    except transactions_service.UnbalancedTransactionError as exc:
        # Defensive: the app already validated DR=CR. If we hit this, our
        # own logic disagreed with Postgres — clean up the file and surface
        # the message verbatim.
        if saved_filename:
            (settings.upload_dir / saved_filename).unlink(missing_ok=True)
        return _render_form(
            request=request,
            user=user,
            transaction_date=transaction_date,
            lines=raw_lines,
            errors=[f"Database rejected the transaction: {exc}"],
            status_code=400,
        )

    flash(
        request,
        f"Transaction {transaction_reference} posted on {txn_date.strftime('%d/%m/%Y')} "
        f"(DR=CR=${dr_total:,.2f}, {len(parsed_lines)} lines).",
        "success",
    )
    return RedirectResponse("/transactions/new", status_code=303)
