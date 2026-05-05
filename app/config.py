"""
Loads configuration from the .env file in the project root and exposes it
as a single `settings` object. Everywhere else in the app imports from here
instead of reading os.environ directly, so we have one place to change config.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

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

    @property
    def db_dsn(self) -> str:
        """psycopg connection string built from the individual fields."""
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.db_name} user={self.db_user} "
            f"password={self.db_password}"
        )


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
    )


# Singleton — import this from anywhere: `from app.config import settings`
settings = _load_settings()
