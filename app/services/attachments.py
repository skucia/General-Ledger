"""
Attachment management for existing transactions: add, replace, delete.

The post-time upload path lives in app/routers/transactions.py; this
service handles the post-post-time operations only. Period locks do
NOT apply (attachments are documentation, not financial substance) —
the trigger refinement in migration 009 lets these UPDATEs through
on locked-period rows.

File-write order is deliberate so a crash mid-op leaves the system in
a sensible state:
  - Add/Replace: write new file -> UPDATE row -> unlink old file (best-effort)
  - Delete:      UPDATE row -> unlink file (best-effort)

If the DB UPDATE fails after we've written a new file, we unlink the
new file before re-raising so we don't leave orphaned bytes on disk.
"""

from pathlib import Path
from typing import Optional, TypedDict
from uuid import uuid4

from app.config import settings
from app.db import get_connection


MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB — matches the post-time limit.


class AttachmentTooLargeError(Exception):
    """Raised when the uploaded file exceeds MAX_ATTACHMENT_BYTES."""


class TransactionNotFoundError(Exception):
    """Raised when the transaction id doesn't exist."""


class AttachmentResult(TypedDict):
    transaction_id: int
    attachment_filename: Optional[str]   # original filename, or None after delete


def _existing_attachment_path(cur, transaction_id: int) -> Optional[str]:
    """
    Return the current `attachment_path` (UUID-based filename) for a
    transaction, or None if the row has no attachment. Raises
    TransactionNotFoundError if the row doesn't exist.
    """
    cur.execute(
        "SELECT attachment_path FROM transactions WHERE id = %s",
        (transaction_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise TransactionNotFoundError(f"Transaction {transaction_id} not found")
    return row[0]


def _unlink_quietly(filename: Optional[str]) -> None:
    """Best-effort delete of a file in upload_dir. Missing files are fine."""
    if not filename:
        return
    (settings.upload_dir / filename).unlink(missing_ok=True)


def replace_attachment(
    transaction_id: int,
    file_bytes: bytes,
    original_name: str,
) -> AttachmentResult:
    """
    Add or replace the attachment on `transaction_id`. If a previous
    attachment exists, its file is unlinked from disk after the DB
    UPDATE succeeds. Returns the new attachment_filename (== the
    original name we display in the UI).
    """
    if len(file_bytes) > MAX_ATTACHMENT_BYTES:
        raise AttachmentTooLargeError(
            f"Attachment is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
        )

    # Generate a fresh UUID-based filename. Always new — even on Replace —
    # because the new file's extension may differ from the old one.
    ext = Path(original_name).suffix
    new_path = f"{uuid4().hex}{ext}"

    # Write the new file BEFORE the DB UPDATE so a write failure can't
    # leave the row pointing at a missing file.
    (settings.upload_dir / new_path).write_bytes(file_bytes)

    try:
        with get_connection() as conn, conn.cursor() as cur:
            old_path = _existing_attachment_path(cur, transaction_id)
            cur.execute(
                """
                UPDATE transactions
                   SET attachment_path = %s,
                       attachment_original_name = %s
                 WHERE id = %s
                """,
                (new_path, original_name, transaction_id),
            )
    except Exception:
        # DB UPDATE failed after we wrote the file — clean it up.
        _unlink_quietly(new_path)
        raise

    # DB committed. Old file is now orphaned — best-effort unlink.
    if old_path and old_path != new_path:
        _unlink_quietly(old_path)

    return {
        "transaction_id": transaction_id,
        "attachment_filename": original_name,
    }


def delete_attachment(transaction_id: int) -> AttachmentResult:
    """
    Remove the attachment from `transaction_id`. NULLs both columns and
    unlinks the file. No-op (returns success) if the transaction has no
    attachment to begin with.
    """
    with get_connection() as conn, conn.cursor() as cur:
        old_path = _existing_attachment_path(cur, transaction_id)
        if old_path is None:
            return {"transaction_id": transaction_id, "attachment_filename": None}
        cur.execute(
            """
            UPDATE transactions
               SET attachment_path = NULL,
                   attachment_original_name = NULL
             WHERE id = %s
            """,
            (transaction_id,),
        )

    # DB committed. Unlink is best-effort; an orphaned file is harmless.
    _unlink_quietly(old_path)

    return {"transaction_id": transaction_id, "attachment_filename": None}
