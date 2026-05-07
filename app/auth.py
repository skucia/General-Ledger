"""
Auth helpers used as FastAPI dependencies.

`get_current_user` looks up the logged-in user on every request. We refetch
from the DB rather than trusting cached session data so role / admin /
must_change_password changes take effect immediately.

The three custom exceptions below are caught by handlers in app/main.py and
turned into HTTP redirects — that's how we send unauthenticated users to
/login, force first-login users to /change-password, and bounce non-admins
away from admin routes.
"""

from fastapi import Depends, Request

from app.config import DATABASES
from app.services import users as users_service


class NotAuthenticated(Exception):
    """No valid session — redirect to /login."""


class MustChangePassword(Exception):
    """User has must_change_password=TRUE — redirect to /change-password."""


class NotAdmin(Exception):
    """Logged-in user is not an admin — bounce back to /menu."""


class NotFullUser(Exception):
    """Logged-in user is view-only — POST submissions are blocked."""


# Pages a "must change password" user is still allowed to reach.
_PASSWORD_CHANGE_ALLOWED_PATHS = {"/change-password", "/logout"}


def get_current_user(request: Request) -> dict:
    """
    Returns the user dict for the logged-in user, or raises a redirect
    exception if there's no session, the user no longer exists, or they
    haven't completed their forced password change yet.

    A valid session requires BOTH user_id AND a db_key in the allowed list.
    Missing/invalid db_key means the user can't be safely looked up — we
    clear the session and force re-login rather than silently falling
    back to the .env-configured database.
    """
    user_id = request.session.get("user_id")
    db_key = request.session.get("db_key")
    if not user_id or db_key not in DATABASES:
        # Wipe any partial state so the next /login starts cleanly.
        request.session.clear()
        raise NotAuthenticated()

    user = users_service.get_user_by_id(user_id)
    if user is None:
        # Session points at a deleted user — clear it and force re-login.
        request.session.clear()
        raise NotAuthenticated()

    if user["must_change_password"] and request.url.path not in _PASSWORD_CHANGE_ALLOWED_PATHS:
        raise MustChangePassword()

    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Same as get_current_user but also requires is_admin=TRUE."""
    if not user["is_admin"]:
        raise NotAdmin()
    return user


def require_full_user(user: dict = Depends(get_current_user)) -> dict:
    """
    Use on POST handlers that mutate data (Add User, Add Account, Add Transaction).
    Logged-in view-only users are bounced back to /menu with a flash message.
    """
    if user["user_type"] != "full":
        raise NotFullUser()
    return user
