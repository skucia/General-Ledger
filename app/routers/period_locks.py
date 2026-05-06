"""
Admin-only Period Locks management.

  GET  /admin/period-locks  — list page: current state + history + form
  POST /admin/period-locks  — handles BOTH phases of the create flow:
                              first submit  -> confirmation panel
                              confirmed=yes -> actually create the lock

Single template `period_locks.html` with a `mode` variable distinguishes
the form view from the confirmation view. All routes gated by
require_admin (non-admins are bounced to /menu).
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import require_admin
from app.services import period_locks as locks_service
from app.templating import flash, templates

router = APIRouter(prefix="/admin/period-locks")


def _default_lock_date() -> date:
    """
    Default value for the form's date input — last day of the previous
    month. Sensible for monthly closes; covers year-end implicitly when
    today is in January.
    """
    today = date.today()
    return today.replace(day=1) - timedelta(days=1)


def _render_page(
    request: Request,
    admin: dict,
    *,
    mode: str = "form",
    form_date: str = "",
    form_reason: str = "",
    proposed_date: Optional[date] = None,
    proposed_reason: str = "",
    txn_count: int = 0,
    errors: Optional[list] = None,
    status_code: int = 200,
):
    """
    Render the period_locks.html template. The template handles either
    `mode='form'` (default) or `mode='confirm'` based on the variable.
    Current state and history are always passed through.
    """
    return templates.TemplateResponse(
        "period_locks.html",
        {
            "request": request,
            "user": admin,
            "current_lock": locks_service.get_current_lock_date(),
            "locks": locks_service.list_locks(),
            "mode": mode,
            "form_date": form_date or _default_lock_date().isoformat(),
            "form_reason": form_reason,
            "proposed_date": proposed_date,
            "proposed_reason": proposed_reason,
            "txn_count": txn_count,
            "errors": errors or [],
        },
        status_code=status_code,
    )


@router.get("")
def period_locks_page(
    request: Request,
    admin: dict = Depends(require_admin),
):
    return _render_page(request, admin)


@router.post("")
def period_locks_submit(
    request: Request,
    admin: dict = Depends(require_admin),
    locked_through: str = Form(default=""),
    reason: str = Form(default=""),
    confirmed: str = Form(default=""),
):
    reason = reason.strip()

    # Parse the date — bail early on a malformed value.
    try:
        date_parsed = date.fromisoformat(locked_through)
    except ValueError:
        return _render_page(
            request, admin,
            form_date=locked_through, form_reason=reason,
            errors=["Lock date is invalid."],
            status_code=400,
        )

    # PHASE 2: user already confirmed — actually create the lock.
    if confirmed == "yes":
        try:
            locks_service.create_lock(date_parsed, admin["id"], reason)
        except (locks_service.LockDateInFutureError,
                locks_service.LockMustMoveForwardError) as exc:
            # Defensive: phase 1 already validated, but conditions could
            # have changed (another admin created a lock in between).
            return _render_page(
                request, admin,
                form_date=locked_through, form_reason=reason,
                errors=[str(exc)],
                status_code=400,
            )
        flash(
            request,
            f"Period locked through {date_parsed.strftime('%d/%m/%Y')}.",
            "success",
        )
        return RedirectResponse("/admin/period-locks", status_code=303)

    # PHASE 1: validate, then render the confirmation panel.
    try:
        locks_service.validate_lock_date(date_parsed)
    except (locks_service.LockDateInFutureError,
            locks_service.LockMustMoveForwardError) as exc:
        return _render_page(
            request, admin,
            form_date=locked_through, form_reason=reason,
            errors=[str(exc)],
            status_code=400,
        )

    txn_count = locks_service.count_transactions_through(date_parsed)
    return _render_page(
        request, admin,
        mode="confirm",
        proposed_date=date_parsed,
        proposed_reason=reason,
        txn_count=txn_count,
    )
