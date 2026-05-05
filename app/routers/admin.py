"""
Admin-only routes.

For Phase 2 there's just one: an admin-driven "reset another user's
password" form. The admin types in the target username and a temporary
password. We set must_change_password=TRUE on the target user so the
next time they log in, they're forced to pick a new password.

Phase 4 will add a full Add Users / list users screen and link to this
form from a per-row "Reset password" button.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import require_admin
from app.security import hash_password
from app.services import users as users_service
from app.templating import flash, templates

router = APIRouter(prefix="/admin")


@router.get("/reset-password")
def reset_password_form(
    request: Request,
    admin: dict = Depends(require_admin),
    username: str = "",  # ?username=X — pre-fills the form when admin
                         # arrives via the per-row link on /users/new
):
    return templates.TemplateResponse(
        "admin_reset_password.html",
        {
            "request": request,
            "user": admin,
            "prefill_username": username,
        },
    )


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    admin: dict = Depends(require_admin),
    username: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    target = users_service.get_user_by_username(username.strip())
    if target is None:
        flash(request, f"No user named '{username}'.", "error")
        return RedirectResponse("/admin/reset-password", status_code=303)

    # An admin must use Change Password to change their own password —
    # this form is for resetting *other* users.
    if target["id"] == admin["id"]:
        flash(
            request,
            "Use Change Password to change your own password.",
            "error",
        )
        return RedirectResponse("/admin/reset-password", status_code=303)

    if new_password != confirm_password:
        flash(request, "Passwords do not match.", "error")
        return RedirectResponse("/admin/reset-password", status_code=303)

    if len(new_password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse("/admin/reset-password", status_code=303)

    users_service.update_password(
        user_id=target["id"],
        password_hash=hash_password(new_password),
        must_change=True,  # force the target to change on next login
    )
    flash(
        request,
        f"Password reset for '{target['username']}'. They must change it on next login.",
        "success",
    )
    return RedirectResponse("/menu", status_code=303)
