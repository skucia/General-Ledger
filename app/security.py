"""
Password hashing helpers. We use passlib's bcrypt scheme because it
handles salting, work-factor tuning, and timing-safe comparison for us.

Only two functions are exposed:
    hash_password(plain)    -> stores this string in users.password_hash
    verify_password(plain, hashed) -> True/False
"""

from passlib.context import CryptContext

# `deprecated="auto"` lets us migrate to a stronger scheme later without breaking existing hashes.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash safe to store in the database."""
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Return True if the plain password matches the stored hash."""
    return _pwd_context.verify(plain_password, password_hash)
