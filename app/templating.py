"""
One Jinja2 templates instance for the whole app, plus tiny "flash message"
helpers. Flash messages are short notices shown after a redirect (e.g.
"Password changed.") and are stored inside the session itself so we don't
need a separate dependency.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# This `templates` object is imported by every router that renders HTML.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# app_version is fixed at startup. db_name/db_env_class are now per-request
# (the user picks at /login) and live on request.state, set by the
# DB-selection middleware in app/main.py.
templates.env.globals["app_version"] = settings.app_version

# Period-lock state — registered as a callable global so each template
# render fetches fresh state via SELECT MAX(locked_through) (one
# indexed query per page render). Used by the lock badge in base.html.
# Imported lazily-ish at module-import time; fine because period_locks.py
# has no other dependencies on this file.
from app.services.period_locks import get_current_lock_date as _get_current_lock_date
templates.env.globals["current_lock_date"] = _get_current_lock_date


def versioned_static(filename: str) -> str:
    """
    Return /static/<filename>?v=<mtime> so browsers automatically re-fetch
    a static asset whenever its file mtime changes. Without this, an
    updated CSS or JS file may sit cached in the browser even after the
    server reloads, causing a stale-asset / new-template mismatch.
    """
    full_path = STATIC_DIR / filename
    try:
        mtime = int(full_path.stat().st_mtime)
    except OSError:
        return f"/static/{filename}"
    return f"/static/{filename}?v={mtime}"


# Make versioned_static usable from any template.
templates.env.globals["versioned_static"] = versioned_static


def flash(request: Request, message: str, category: str = "info") -> None:
    """
    Queue a one-time message to be shown on the next page render.
    `category` is "info" / "success" / "error" — used for CSS styling.
    """
    bucket = request.session.setdefault("_flashes", [])
    bucket.append({"category": category, "message": message})


def get_flashed_messages(request: Request) -> list:
    """Pop and return any pending flash messages (used inside templates)."""
    return request.session.pop("_flashes", [])


# Make get_flashed_messages callable from inside any template.
templates.env.globals["get_flashed_messages"] = get_flashed_messages


def _format_date_dmy(value: Any) -> str:
    """
    Jinja filter: render any date/datetime/ISO-string as dd/mm/yyyy.
    Returns '' for None and falls back to str(value) for unparseable input.
    """
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    try:
        return date.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return str(value)


# Usage in templates: {{ created_at|date_dmy }}
templates.env.filters["date_dmy"] = _format_date_dmy


def _format_money(value: Any) -> str:
    """
    Jinja filter: render a Decimal/float/int amount with thousands separators
    and two decimal places, e.g. 92000 -> '92,000.00'. Returns '' for None.
    Mirrors the backend `_fmt_amt` used by the report routes.
    """
    if value is None:
        return ""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return f"{value:,.2f}"


# Usage in templates: {{ amount|money }}
templates.env.filters["money"] = _format_money
