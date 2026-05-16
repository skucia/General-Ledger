"""
Per-username login rate limiter — in-memory, single-process.

Tracks recent failed-password attempts per (db_key, username) pair and
locks the user out for LOGIN_LOCKOUT_MINUTES after LOGIN_MAX_ATTEMPTS
failures within that window. Sliding window: as old attempts age past
the window, they drop out and the lockout naturally clears.

Scope of "failed attempt":
  - "username exists in chosen DB, wrong password"            -> counted
  - "username exists in chosen DB, correct password but wrong DB" -> counted
    (same code path: verify_password against this DB's hash fails)
  - "username does not exist in chosen DB"                    -> NOT counted
    (would enable username-enumeration via lockout side-channels)

Persistence: state is held in a module-level dict guarded by a Lock.
Lost on process restart by design — keeps the implementation simple
and consistent with the "kept deliberately simple" framing. Determined
attackers could trigger a restart, but anyone with that level of access
has bigger leverage than login rate limits anyway.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.config import settings


_Key = Tuple[str, str]  # (db_key, normalised_username)
_failures: Dict[_Key, List[datetime]] = {}
_lock = threading.Lock()


def _normalise(db_key: str, username: str) -> _Key:
    """Same casing/whitespace normalisation in every public function."""
    return (db_key, username.strip().lower())


def _window_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=settings.login_lockout_minutes)


def check_login_lockout(db_key: str, username: str) -> Optional[int]:
    """
    If the user is currently locked out, return the number of full minutes
    they should wait (always >= 1). Otherwise return None.
    """
    key = _normalise(db_key, username)
    with _lock:
        attempts = _failures.get(key, [])
        cutoff = _window_cutoff()
        recent = [t for t in attempts if t > cutoff]
        if len(recent) < settings.login_max_attempts:
            return None

        # Locked. Unlock when the Nth-most-recent failure ages out of the window.
        recent.sort()
        nth_most_recent = recent[-settings.login_max_attempts]
        unlock_at = nth_most_recent + timedelta(minutes=settings.login_lockout_minutes)
        remaining_seconds = (unlock_at - datetime.now(timezone.utc)).total_seconds()
        # Round up so we never report "0 minutes" while still locked.
        return max(1, int(remaining_seconds // 60) + 1)


def record_login_failure(db_key: str, username: str) -> None:
    """Append a failure timestamp; trims stale entries opportunistically."""
    key = _normalise(db_key, username)
    now = datetime.now(timezone.utc)
    cutoff = _window_cutoff()
    with _lock:
        existing = [t for t in _failures.get(key, []) if t > cutoff]
        existing.append(now)
        _failures[key] = existing


def clear_login_failures(db_key: str, username: str) -> None:
    """Wipe a user's failure history. Called on successful login."""
    key = _normalise(db_key, username)
    with _lock:
        _failures.pop(key, None)
