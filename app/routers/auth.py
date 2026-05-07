"""
Auth-related HTTP routes:
  GET/POST /login            — show form and accept credentials
  POST     /logout           — clear session and redirect to /login
  GET/POST /change-password  — let any logged-in user change their own password

The `must_change_password` flow is handled here too: after a successful
login, if the user's flag is set we send them straight to /change-password.
The dependency in app/auth.py also blocks every other route until they do.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import get_current_user
from app.config import DATABASES
from app.db import set_active_db
from app.security import hash_password, verify_password
from app.services import users as users_service
from app.templating import flash, templates

router = APIRouter()


# --- Login -----------------------------------------------------------------

@router.get("/login")
def login_form(request: Request):
    # If already authenticated, skip the form.
    if request.session.get("user_id"):
        return RedirectResponse("/menu", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "user": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    database: str = Form(...),
):
    # Validate the DB selection against the hardcoded allowed list. A bad
    # value (browser bug, hand-edited form) gets the same generic error
    # so we don't leak which keys exist.
    if database not in DATABASES:
        flash(request, "Invalid username or password.", "error")
        return RedirectResponse("/login", status_code=303)

    # Run the user lookup against the user-selected database. The session
    # doesn't have db_key yet, so the middleware hasn't set the ContextVar
    # — we set it explicitly for this block.
    with set_active_db(database):
        user = users_service.get_user_by_username(username.strip())
        # Same generic error for "no such user", "wrong password", AND
        # "user exists in the OTHER database" — don't leak which DB has
        # which usernames.
        if user is None or not verify_password(password, user["password_hash"]):
            flash(request, "Invalid username or password.", "error")
            return RedirectResponse("/login", status_code=303)

    # Successful login — store user_id AND db_key; the rest is re-fetched per request.
    request.session["user_id"] = user["id"]
    request.session["db_key"] = database

    if user["must_change_password"]:
        flash(request, "Please choose a new password before continuing.", "info")
        return RedirectResponse("/change-password", status_code=303)

    flash(request, f"Welcome, {user['username']}.", "success")
    return RedirectResponse("/menu", status_code=303)


# --- Logout ----------------------------------------------------------------

@router.post("/logout")
def logout(request: Request):
    # Clearing the session invalidates the cookie's signed payload.
    request.session.clear()
    flash(request, "Logged out.", "info")
    return RedirectResponse("/login", status_code=303)


# --- Change password (self-service) ---------------------------------------

@router.get("/change-password")
def change_password_form(
    request: Request,
    user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse(
        "change_password.html",
        {
            "request": request,
            "user": user,
            "force": user["must_change_password"],  # show "you must change" notice
        },
    )


@router.post("/change-password")
def change_password_submit(
    request: Request,
    user: dict = Depends(get_current_user),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    # Validate, flashing a friendly error and re-rendering on any failure.
    if not verify_password(current_password, user["password_hash"]):
        flash(request, "Current password is incorrect.", "error")
        return RedirectResponse("/change-password", status_code=303)

    if new_password != confirm_password:
        flash(request, "New password and confirmation do not match.", "error")
        return RedirectResponse("/change-password", status_code=303)

    if len(new_password) < 8:
        flash(request, "New password must be at least 8 characters.", "error")
        return RedirectResponse("/change-password", status_code=303)

    if new_password == current_password:
        flash(request, "New password must be different from the current password.", "error")
        return RedirectResponse("/change-password", status_code=303)

    # All good — store the hash and clear the must-change flag.
    users_service.update_password(
        user_id=user["id"],
        password_hash=hash_password(new_password),
        must_change=False,
    )
    flash(request, "Password changed.", "success")
    return RedirectResponse("/menu", status_code=303)
