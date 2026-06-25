"""
Attachment management for transactions: list, add, delete.

Attachments live in the `transaction_attachments` child table — one row
per file, many per transaction (capped at MAX_ATTACHMENTS_PER_TXN). The
post-time upload path (app/routers/transactions.py via
transactions_service.post_transaction) inserts the first file; this
service handles everything after a transaction is posted, plus the cap.

Period locks do NOT apply: attachments are documentation, not financial
substance, and writes target the child table (the transactions
period-lock trigger never fires for them).

File-write order is deliberate so a crash mid-op leaves a sensible state:
  - Add:    write new file -> INSERT row  (unlink the new file if INSERT fails)
  - Delete: DELETE row -> unlink file (best-effort; an orphan is harmless)
"""

from pathlib import Path
from typing import List, Optional, TypedDict
from uuid import uuid4

from app.config import settings
from app.db import get_connection


MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024   # 10 MB per file — matches the post-time limit.
MAX_ATTACHMENTS_PER_TXN = 5               # Hard cap on files per transaction.


class AttachmentTooLargeError(Exception):
    """Raised when an uploaded file exceeds MAX_ATTACHMENT_BYTES."""


class TooManyAttachmentsError(Exception):
    """Raised when a transaction already has MAX_ATTACHMENTS_PER_TXN files."""


class TransactionNotFoundError(Exception):
    """Raised when the transaction id doesn't exist."""


class AttachmentNotFoundError(Exception):
    """Raised when the attachment id doesn't exist."""


class AttachmentInfo(TypedDict):
    id: int
    filename: str            # original (display) filename


class AddResult(TypedDict):
    id: int
    transaction_id: int
    attachment_filename: str


def _unlink_quietly(filename: Optional[str]) -> None:
    """Best-effort delete of a file in upload_dir. Missing files are fine."""
    if not filename:
        return
    (settings.upload_dir / filename).unlink(missing_ok=True)


def list_attachments(transaction_id: int) -> List[AttachmentInfo]:
    """Return a transaction's attachments (id + display name), oldest first."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, attachment_original_name
              FROM transaction_attachments
             WHERE transaction_id = %s
             ORDER BY id ASC
            """,
            (transaction_id,),
        )
        return [{"id": r[0], "filename": r[1]} for r in cur.fetchall()]


def add_attachment(
    transaction_id: int,
    file_bytes: bytes,
    original_name: str,
    uploaded_by: Optional[int] = None,
) -> AddResult:
    """
    Add one attachment to `transaction_id`. Enforces the per-file size cap
    and the per-transaction count cap. Returns the new attachment's id and
    display name.

    Raises:
      AttachmentTooLargeError   — file exceeds MAX_ATTACHMENT_BYTES
      TransactionNotFoundError  — transaction id doesn't exist
      TooManyAttachmentsError   — already at MAX_ATTACHMENTS_PER_TXN
    """
    if len(file_bytes) > MAX_ATTACHMENT_BYTES:
        raise AttachmentTooLargeError(
            f"Attachment is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
        )

    # Fresh UUID-based filename — extension preserved for content-type guessing.
    ext = Path(original_name).suffix
    new_path = f"{uuid4().hex}{ext}"

    # Write the file BEFORE the INSERT so a write failure can't leave a row
    # pointing at a missing file. Any error below unlinks it again.
    (settings.upload_dir / new_path).write_bytes(file_bytes)

    try:
        with get_connection() as conn, conn.cursor() as cur:
            # Transaction must exist...
            cur.execute("SELECT 1 FROM transactions WHERE id = %s", (transaction_id,))
            if cur.fetchone() is None:
                raise TransactionNotFoundError(f"Transaction {transaction_id} not found")

            # ...and not already be at the cap. The count is a documentation
            # guardrail, not a financial invariant — a racing concurrent add
            # could in theory slip past it, which is acceptable.
            cur.execute(
                "SELECT COUNT(*) FROM transaction_attachments WHERE transaction_id = %s",
                (transaction_id,),
            )
            if cur.fetchone()[0] >= MAX_ATTACHMENTS_PER_TXN:
                raise TooManyAttachmentsError(
                    f"This transaction already has the maximum of "
                    f"{MAX_ATTACHMENTS_PER_TXN} attachments."
                )

            cur.execute(
                """
                INSERT INTO transaction_attachments
                    (transaction_id, attachment_path, attachment_original_name, uploaded_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (transaction_id, new_path, original_name, uploaded_by),
            )
            new_id = cur.fetchone()[0]
    except Exception:
        _unlink_quietly(new_path)
        raise

    return {
        "id": new_id,
        "transaction_id": transaction_id,
        "attachment_filename": original_name,
    }


def delete_attachment(attachment_id: int) -> None:
    """
    Delete one attachment by its id and unlink its file. Raises
    AttachmentNotFoundError if the id doesn't exist.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT attachment_path FROM transaction_attachments WHERE id = %s",
            (attachment_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} not found")
        old_path = row[0]
        cur.execute(
            "DELETE FROM transaction_attachments WHERE id = %s",
            (attachment_id,),
        )

    # DB committed. Unlink is best-effort — an orphaned file is harmless.
    _unlink_quietly(old_path)
