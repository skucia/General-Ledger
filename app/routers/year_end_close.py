"""
Admin-only Year-End Close.

  GET  /admin/year-end-close  — date picker + Preview button
  POST /admin/year-end-close  — single endpoint, two phases:
                                first submit  -> validate + render confirm
                                confirmed=yes -> atomic close + redirect

Single template `year_end_close.html` with a `mode` variable
distinguishes the form view from the confirm view. All routes gated
by require_admin (non-admins are bounced to /menu).
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import require_admin
from app.services import year_end_close as close_service
from app.templating import flash, templates

router = APIRouter(prefix="/admin/year-end-close")


def _default_close_date() -> date:
    """31 December of the previous calendar year."""
    today = date.today()
    return date(today.year - 1, 12, 31)


def _render_page(
    request: Request,
    admin: dict,
    *,
    mode: str = "form",
    form_date: str = "",
    preview: Optional[dict] = None,
    errors: Optional[list] = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "year_end_close.html",
        {
            "request": request,
            "user": admin,
            "mode": mode,
            "form_date": form_date or _default_close_date().isoformat(),
            "preview": preview,
            "errors": errors or [],
        },
        status_code=status_code,
    )


@router.get("")
def year_end_close_page(
    request: Request,
    admin: dict = Depends(require_admin),
):
    return _render_page(request, admin)


@router.post("")
def year_end_close_submit(
    request: Request,
    admin: dict = Depends(require_admin),
    close_date: str = Form(default=""),
    confirmed: str = Form(default=""),
):
    # Parse the date — bail early on malformed.
    try:
        date_parsed = date.fromisoformat(close_date)
    except ValueError:
        return _render_page(
            request, admin,
            form_date=close_date,
            errors=["Close date is invalid."],
            status_code=400,
        )

    # PHASE 2: confirmed — execute the atomic close.
    if confirmed == "yes":
        try:
            close_service.execute_close(date_parsed, admin["id"])
        except close_service.YearEndClosePreFlightError as exc:
            return _render_page(
                request, admin,
                form_date=close_date,
                errors=[str(exc)],
                status_code=400,
            )
        flash(
            request,
            f"Year-end close complete. Period locked through "
            f"{date_parsed.strftime('%d/%m/%Y')}.",
            "success",
        )
        # Land on the period-locks page so the user can see the new lock
        # in context (and the new lock badge in the header).
        return RedirectResponse("/admin/period-locks", status_code=303)

    # PHASE 1: validate + compute the closing journal, render confirm.
    try:
        preview = close_service.preview_close(date_parsed)
    except close_service.YearEndClosePreFlightError as exc:
        return _render_page(
            request, admin,
            form_date=close_date,
            errors=[str(exc)],
            status_code=400,
        )

    return _render_page(
        request, admin,
        mode="confirm",
        form_date=close_date,
        preview=preview,
    )
