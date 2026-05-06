"""
FastAPI application entry point.

Wires together:
  - configuration (.env via app.config),
  - signed-cookie sessions,
  - the static files directory,
  - the auth-redirect exception handlers,
  - and the routers (menu, auth, admin).

Phases 4–7 will add more routers (users, accounts, transactions, reports).
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import MustChangePassword, NotAdmin, NotAuthenticated, NotFullUser
from app.config import settings
from app.routers import accounts as accounts_router
from app.routers import admin as admin_router
from app.routers import attachments as attachments_router
from app.routers import auth as auth_router
from app.routers import menu as menu_router
from app.routers import period_locks as period_locks_router
from app.routers import reports as reports_router
from app.routers import transactions as transactions_router
from app.routers import users as users_router
from app.templating import flash

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="General Ledger", version="0.2.0")

# Sign session cookies with the SESSION_SECRET from .env.
# https_only=False because we run on localhost http during development.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=False,
)

# Serve our small CSS file (and any future static assets) from /static/...
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Exception handlers: turn auth failures into HTTP redirects ------------

@app.exception_handler(NotAuthenticated)
async def _not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(MustChangePassword)
async def _must_change_password_handler(request: Request, exc: MustChangePassword):
    return RedirectResponse("/change-password", status_code=303)


@app.exception_handler(NotAdmin)
async def _not_admin_handler(request: Request, exc: NotAdmin):
    flash(request, "You need admin access to do that.", "error")
    return RedirectResponse("/menu", status_code=303)


@app.exception_handler(NotFullUser)
async def _not_full_user_handler(request: Request, exc: NotFullUser):
    flash(request, "View-only users cannot submit changes.", "error")
    return RedirectResponse("/menu", status_code=303)


# --- Mount routers ---------------------------------------------------------

app.include_router(menu_router.router)
app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(users_router.router)
app.include_router(accounts_router.router)
app.include_router(transactions_router.router)
app.include_router(reports_router.router)
app.include_router(attachments_router.router)
app.include_router(period_locks_router.router)
