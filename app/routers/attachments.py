"""
Transaction attachments — view, add, delete. Many per transaction.

  GET   /attachments/{attachment_id}               — serve a file inline (any user)
  POST  /attachments/transaction/{transaction_id}  — add a file (full users)
  POST  /attachments/{attachment_id}/delete        — remove a file (full users)

Security:
  - Login required (get_current_user) for view; full-user role
    (require_full_user) for add/delete — view-only users get a
    redirect-to-/menu via the NotFullUser handler.
  - URL paths take integer ids; the on-disk filename (UUID-based) is
    looked up from the DB, so users can never influence which file is
    served beyond pointing at an attachment id.

Period locks: deliberately NOT enforced — attachments are documentation,
and writes target the transaction_attachments child table (the
transactions period-lock trigger never fires for them).

Browsers: GET sets Content-Disposition: inline (so PDFs / images render
in-tab) and includes the original filename via RFC 6266 percent-encoded
filename* so saving keeps the friendly name.
"""

import mimetypes
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row

from app.auth import get_current_user, require_full_user
from app.config import settings
from app.db import get_connection
from app.services import attachments as attachments_service

router = APIRouter()


@router.get("/attachments/{attachment_id}")
def serve_attachment(
    attachment_id: int,
    user: dict = Depends(get_current_user),
):
    # 1. Look up the stored filename + original name from the DB.
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT attachment_path, attachment_original_name
              FROM transaction_attachments
             WHERE id = %s
            """,
            (attachment_id,),
        )
        row = cur.fetchone()

    if row is None or not row["attachment_path"]:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # 2. Resolve the file on disk. attachment_path is a bare UUID-based
    #    filename (we never let user input flow into this), so it can't
    #    escape the upload dir — but we do a final containment check.
    file_path = (settings.upload_dir / row["attachment_path"]).resolve()
    upload_root = settings.upload_dir.resolve()
    try:
        file_path.relative_to(upload_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Attachment file missing on disk")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing on disk")

    # 3. Content-Type from the original filename's extension so PDFs /
    #    images / text render in-browser rather than download.
    original_name = row["attachment_original_name"] or row["attachment_path"]
    media_type, _ = mimetypes.guess_type(original_name)
    if media_type is None:
        media_type = "application/octet-stream"

    # 4. inline disposition; RFC 6266 filename* preserves non-ASCII names.
    encoded_name = quote(original_name)
    content_disposition = f"inline; filename*=UTF-8''{encoded_name}"

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


# --- Add -------------------------------------------------------------------

@router.post("/attachments/transaction/{transaction_id}")
async def add_attachment(
    transaction_id: int,
    user: dict = Depends(require_full_user),
    file: Optional[UploadFile] = File(None),
):
    """
    Add one attachment to a transaction (up to MAX_ATTACHMENTS_PER_TXN).
    JSON response:
        { "ok": true, "id": int, "transaction_id": int,
          "attachment_filename": str }
    """
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    file_bytes = await file.read()

    try:
        result = attachments_service.add_attachment(
            transaction_id=transaction_id,
            file_bytes=file_bytes,
            original_name=file.filename,
            uploaded_by=user["id"],
        )
    except attachments_service.TransactionNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    except attachments_service.AttachmentTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except attachments_service.TooManyAttachmentsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return JSONResponse({"ok": True, **result})


# --- Delete ----------------------------------------------------------------

@router.post("/attachments/{attachment_id}/delete")
def delete_attachment(
    attachment_id: int,
    user: dict = Depends(require_full_user),
):
    """
    Remove a single attachment by its id. JSON response:
        { "ok": true, "attachment_id": int }
    """
    try:
        attachments_service.delete_attachment(attachment_id)
    except attachments_service.AttachmentNotFoundError:
        raise HTTPException(status_code=404, detail="Attachment not found.")

    return JSONResponse({"ok": True, "attachment_id": attachment_id})
