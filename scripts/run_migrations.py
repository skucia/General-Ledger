"""
Migration runner.

Applies every *.sql file in the migrations/ folder, in filename order, exactly
once. A small `schema_migrations` table records which files have already run so
this script is safe to re-run any time.

Run from the project root:
    python -m scripts.run_migrations
"""

import sys
from pathlib import Path

# Make sure imports work whether you run this as a module or as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_connection  # noqa: E402

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def ensure_tracking_table(cur) -> None:
    """Create the bookkeeping table if it doesn't exist yet."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def already_applied(cur, filename: str) -> bool:
    cur.execute("SELECT 1 FROM schema_migrations WHERE filename = %s", (filename,))
    return cur.fetchone() is not None


def apply_migration(cur, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    cur.execute(sql)
    cur.execute(
        "INSERT INTO schema_migrations (filename) VALUES (%s)",
        (path.name,),
    )


def main() -> int:
    if not MIGRATIONS_DIR.exists():
        print(f"No migrations folder found at {MIGRATIONS_DIR}")
        return 1

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        print("No *.sql migrations to apply.")
        return 0

    with get_connection() as conn, conn.cursor() as cur:
        ensure_tracking_table(cur)

        for path in sql_files:
            if already_applied(cur, path.name):
                print(f"SKIP  {path.name} (already applied)")
                continue
            print(f"APPLY {path.name} ...", end=" ", flush=True)
            apply_migration(cur, path)
            print("ok")

    print("\nMigrations complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
