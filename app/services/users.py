"""
Database queries for the users table.

Putting these in a service module (instead of inline in routers) keeps the
business logic separated from the HTTP layer — a future React frontend can
call these same functions without rewriting them.

Each function opens its own connection. That's fine for this app's traffic;
we can add a connection pool later if needed.
"""

from typing import List, Optional

from psycopg.rows import dict_row

from app.db import get_connection


class UsernameTakenError(Exception):
    """Raised by create_user when the chosen username already exists."""

    def __init__(self, username: str):
        super().__init__(f"username '{username}' already exists")
        self.username = username


# Columns we expose to the rest of the app. password_hash is included because
# the auth flow needs it; never render it back to the user.
_USER_COLS = """
    id, username, email, password_hash,
    user_type, is_admin, must_change_password,
    created_at
"""


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Returns the user row as a dict, or None if not found."""
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_USER_COLS} FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


def get_user_by_username(username: str) -> Optional[dict]:
    """Returns the user row as a dict, or None if not found."""
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_USER_COLS} FROM users WHERE username = %s",
            (username,),
        )
        return cur.fetchone()


def list_users() -> List[dict]:
    """All users, ordered by username. Used by the admin Add Users screen."""
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_USER_COLS} FROM users ORDER BY username")
        return cur.fetchall()


def create_user(
    username: str,
    email: str,
    password_hash: str,
    user_type: str,
    is_admin: bool,
) -> int:
    """
    Insert a new user with `must_change_password=TRUE` so they're forced
    to pick a new password the first time they log in.

    Raises:
        UsernameTakenError: if `username` already exists.

    Returns the new user's id.
    """
    with get_connection() as conn, conn.cursor() as cur:
        # Check up front so we can raise a typed error instead of leaking a
        # Postgres unique-violation traceback to the route handler.
        cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            raise UsernameTakenError(username)

        cur.execute(
            """
            INSERT INTO users (
                username, email, password_hash,
                user_type, is_admin, must_change_password
            )
            VALUES (%s, %s, %s, %s, %s, TRUE)
            RETURNING id
            """,
            (username, email, password_hash, user_type, is_admin),
        )
        return cur.fetchone()[0]


def update_password(user_id: int, password_hash: str, must_change: bool) -> None:
    """
    Set a new password hash on a user.
    `must_change=True` is used by the admin reset flow so the affected user
    is forced to pick a new password the next time they log in.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
               SET password_hash = %s,
                   must_change_password = %s
             WHERE id = %s
            """,
            (password_hash, must_change, user_id),
        )
