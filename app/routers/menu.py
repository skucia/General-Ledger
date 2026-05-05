"""
The main menu (post-login landing page).

In Phase 2 this is just a placeholder so logged-in users have somewhere to
land. Phase 3 replaces the contents of menu.html with the real button menu
(Add Users, Add Accounts, Add Transactions, Run Reports, Logout).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.auth import get_current_user
from app.templating import templates

router = APIRouter()


@router.get("/")
def index(request: Request):
    """Root URL: send anonymous users to /login, logged-in users to /menu."""
    if request.session.get("user_id"):
        return RedirectResponse("/menu", status_code=303)
    return RedirectResponse("/login", status_code=303)


@router.get("/menu")
def menu(request: Request, user: dict = Depends(get_current_user)):
    return templates.TemplateResponse(
        "menu.html",
        {"request": request, "user": user},
    )
