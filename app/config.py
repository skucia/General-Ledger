"""
Loads configuration from the .env file in the project root and exposes it
as a single `settings` object. Everywhere else in the app imports from here
instead of reading os.environ directly, so we have one place to change config.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

# Available databases for in-app selection at login. Hardcoded by design —
# adding a new DB means a deliberate code change. The KEY ('test'/'live')
# is what gets stored in the session; the VALUE is the actual Postgres
# database name. The shared host/port/user/password come from .env.
DATABASES: Dict[str, str] = {
    "test": "generalledger_test",
    "live": "generalledger_live",
}

# Find the project root (one level up from this file's directory: app/ -> project/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Read the .env file at the project root and put its values into os.environ.
# If a variable is already set in the real environment, that wins (handy for prod).
load_dotenv(PROJECT_ROOT / ".env")


def _required(name: str) -> str:
    """Helper: fetch an env var and fail loudly if it's missing or blank."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Check your .env file in {PROJECT_ROOT}."
        )
    return value


@dataclass(frozen=True)
class Settings:
    """All app configuration in one immutable bundle."""

    # Database
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    # Web / sessions
    session_secret: str

    # File uploads — resolved to an absolute path so it works no matter where you run the app from
    upload_dir: Path

    # App version (read from VERSION file at startup; falls back to "0.0.0-dev"
    # if the file is missing so the app boots regardless).
    app_version: str

    def _build_dsn(self, db_name: str) -> str:
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={db_name} user={self.db_user} "
            f"password={self.db_password}"
        )

    @property
    def db_dsn(self) -> str:
        """
        psycopg DSN for the .env-configured database. Used by system scripts
        (run_migrations, create_admin) and as a fallback inside get_connection
        when no per-request database has been selected.
        """
        return self._build_dsn(self.db_name)

    def db_dsn_for(self, db_key: str) -> str:
        """
        psycopg DSN for one of the in-app selectable databases.
        `db_key` must be a key in app.config.DATABASES ('test' or 'live').
        Shares host/port/user/password with .env; only the dbname differs.
        """
        if db_key not in DATABASES:
            raise ValueError(
                f"Unknown database key: {db_key!r}. "
                f"Allowed: {sorted(DATABASES)}"
            )
        return self._build_dsn(DATABASES[db_key])


def _read_version() -> str:
    """Read VERSION at startup. Single line, trimmed. Fall back if missing."""
    version_file = PROJECT_ROOT / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0-dev"
    except FileNotFoundError:
        return "0.0.0-dev"


def _load_settings() -> Settings:
    upload_dir_raw = os.getenv("UPLOAD_DIR", "./uploads").strip()
    upload_dir = (PROJECT_ROOT / upload_dir_raw).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        db_host=_required("DB_HOST"),
        db_port=int(_required("DB_PORT")),
        db_name=_required("DB_NAME"),
        db_user=_required("DB_USER"),
        db_password=_required("DB_PASSWORD"),
        session_secret=_required("SESSION_SECRET"),
        upload_dir=upload_dir,
        app_version=_read_version(),
    )


# Singleton — import this from anywhere: `from app.config import settings`
settings = _load_settings()
