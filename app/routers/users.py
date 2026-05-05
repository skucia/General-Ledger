"""
User-management routes (admin-only).

  GET  /users/new   — Add User form + table of existing users
  POST /users/new   — Create a new user (admin AND user_type='full' required)

A view-only admin (rare combo) can SEE this page but their POST is blocked
by the require_full_user dependency. That matches the role model the user
confirmed: is_admin gates visibility, user_type gates submissions.

New users are always created with must_change_password=TRUE so the temp
password forces a change on first login.
"""

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import require_admin, require_full_user
from app.security import hash_password
from app.services import users as users_service
from app.templating import flash, templates

router = APIRouter()

# Allowed username characters: letters, digits, underscore, hyphen, 1–30 chars.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,30}$")

# Loose email regex — matches the one used in the bootstrap admin script.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/users/new")
def add_user_form(
    request: Request,
    admin: dict = Depends(require_admin),
):
    return templates.TemplateResponse(
        "add_user.html",
        {
            "request": request,
            "user": admin,
            "existing_users": users_service.list_users(),
        },
    )


@router.post("/users/new")
def add_user_submit(
    request: Request,
    admin: dict = Depends(require_admin),
    # require_full_user blocks view-only admins from submitting — but
    # they can still GET the form (above) and see the user list.
    _full: dict = Depends(require_full_user),
    username: str = Form(...),
    email: str = Form(...),
    user_type: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    is_admin: bool = Form(False),  # checkbox: missing -> False
):
    username = username.strip()
    email = email.strip()

    # --- Validation -------------------------------------------------------
    # On any error we flash a message and redirect back so the form re-renders.
    # The user has to retype password fields either way; other fields will be
    # cleared too — acceptable for now, can be improved later if it bothers you.

    if not _USERNAME_RE.match(username):
        flash(
            request,
            "Username must be letters, digits, underscore or hyphen, 1–30 characters.",
            "error",
        )
        return RedirectResponse("/users/new", status_code=303)

    if not _EMAIL_RE.match(email):
        flash(request, "That doesn't look like a valid email address.", "error")
        return RedirectResponse("/users/new", status_code=303)

    if user_type not in ("full", "view"):
        flash(request, "User type must be 'full' or 'view'.", "error")
        return RedirectResponse("/users/new", status_code=303)

    if password != confirm_password:
        flash(request, "Passwords do not match.", "error")
        return RedirectResponse("/users/new", status_code=303)

    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse("/users/new", status_code=303)

    # --- Insert -----------------------------------------------------------
    try:
        new_id = users_service.create_user(
            username=username,
            email=email,
            password_hash=hash_password(password),
            user_type=user_type,
            is_admin=is_admin,
        )
    except users_service.UsernameTakenError:
        flash(request, f"Username '{username}' is already taken.", "error")
        return RedirectResponse("/users/new", status_code=303)

    flash(
        request,
        f"User '{username}' created (id={new_id}). They must change their password on first login.",
        "success",
    )
    return RedirectResponse("/users/new", status_code=303)
