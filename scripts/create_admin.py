"""
Interactive bootstrap script: creates the very first Admin user.

Why this exists: the login screen (Phase 2) won't let anyone in until at least
one admin exists in the users table. This script prompts for username, email,
and password, then inserts the row with is_admin=TRUE and user_type='full'.

Run from the project root (after run_migrations.py):
    python -m scripts.create_admin
"""

import getpass
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_connection  # noqa: E402
from app.security import hash_password  # noqa: E402


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def prompt_nonblank(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("  -> cannot be blank, try again")


def prompt_email() -> str:
    while True:
        value = input("Email: ").strip()
        if EMAIL_RE.match(value):
            return value
        print("  -> that doesn't look like a valid email, try again")


def prompt_password() -> str:
    while True:
        pw1 = getpass.getpass("Password (min 8 chars, hidden): ")
        if len(pw1) < 8:
            print("  -> too short, try again")
            continue
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 != pw2:
            print("  -> passwords don't match, try again")
            continue
        return pw1


def main() -> int:
    print("=== Create the first Admin user ===")
    print("(This is the bootstrap script. Re-running it lets you add more admins")
    print(" but you can also do that from the Add Users screen later.)\n")

    username = prompt_nonblank("Username")
    email = prompt_email()
    password = prompt_password()

    pw_hash = hash_password(password)

    with get_connection() as conn, conn.cursor() as cur:
        # Check uniqueness up front so the user gets a friendly message
        # instead of a Postgres unique-violation traceback.
        cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            print(f"\nERROR: a user named '{username}' already exists.")
            return 1

        cur.execute(
            """
            INSERT INTO users (
                username, email, password_hash,
                user_type, is_admin, must_change_password
            )
            VALUES (%s, %s, %s, 'full', TRUE, FALSE)
            RETURNING id
            """,
            (username, email, pw_hash),
        )
        new_id = cur.fetchone()[0]

    print(f"\nAdmin user '{username}' created (id={new_id}). You can now log in once Phase 2 is built.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
