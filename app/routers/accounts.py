"""
Chart-of-accounts routes.

  GET  /accounts/new   — form + grouped table of existing accounts
  POST /accounts/new   — create a new account (any logged-in user_type='full' user)

A view-only user can SEE the form (per the role model) but submissions are
blocked by `require_full_user`. The same dependency is applied in Phase 6.

Account number rules:
  - alphanumeric only, 1–20 chars
  - case-insensitive uniqueness (we uppercase before insert)
"""

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import get_current_user, require_full_user
from app.services import accounts as accounts_service
from app.templating import flash, templates

router = APIRouter()

# Alphanumeric only, 1–20 chars. Lowercase is allowed at input time; we
# uppercase before persisting.
_ACCOUNT_NUMBER_RE = re.compile(r"^[A-Za-z0-9]{1,20}$")

_VALID_ACCOUNT_TYPES = set(accounts_service.ACCOUNT_TYPE_ORDER)  # {'A','L','E','S','C'}


@router.get("/accounts/new")
def add_account_form(
    request: Request,
    user: dict = Depends(get_current_user),
):
    accounts = accounts_service.list_accounts()
    grouped = accounts_service.group_accounts_by_type(accounts)
    return templates.TemplateResponse(
        "add_account.html",
        {
            "request": request,
            "user": user,
            "grouped_accounts": grouped,
            # Used to render the type dropdown in accounting order.
            "type_order": accounts_service.ACCOUNT_TYPE_ORDER,
            "type_labels": accounts_service.ACCOUNT_TYPE_LABELS,
        },
    )


@router.post("/accounts/new")
def add_account_submit(
    request: Request,
    user: dict = Depends(require_full_user),  # blocks view-only submissions
    account_number: str = Form(...),
    account_name: str = Form(...),
    account_type: str = Form(...),
):
    # Trim whitespace; uppercase the account number per the spec decision.
    account_number = account_number.strip().upper()
    account_name = account_name.strip()
    account_type = account_type.strip().upper()

    # --- Validation -------------------------------------------------------

    if not _ACCOUNT_NUMBER_RE.match(account_number):
        flash(
            request,
            "Account Number must be 1–20 alphanumeric characters.",
            "error",
        )
        return RedirectResponse("/accounts/new", status_code=303)

    if not account_name:
        flash(request, "Account Name is required.", "error")
        return RedirectResponse("/accounts/new", status_code=303)

    if len(account_name) > 30:
        flash(request, "Account Name must be 30 characters or fewer.", "error")
        return RedirectResponse("/accounts/new", status_code=303)

    if account_type not in _VALID_ACCOUNT_TYPES:
        flash(
            request,
            "Account Type must be S, C, A, L, or E.",
            "error",
        )
        return RedirectResponse("/accounts/new", status_code=303)

    # --- Insert -----------------------------------------------------------

    try:
        saved_number = accounts_service.create_account(
            account_number=account_number,
            account_name=account_name,
            account_type=account_type,
        )
    except accounts_service.AccountNumberTakenError:
        flash(
            request,
            f"Account Number '{account_number}' already exists.",
            "error",
        )
        return RedirectResponse("/accounts/new", status_code=303)

    flash(
        request,
        f"Account '{saved_number}' ({account_name}) created.",
        "success",
    )
    return RedirectResponse("/accounts/new", status_code=303)
