"""
Transaction attachments — view, add, replace, delete.

  GET   /attachments/{transaction_id}         — serve the file inline (any user)
  POST  /attachments/{transaction_id}         — add or replace (full users)
  POST  /attachments/{transaction_id}/delete  — remove (full users)

Security:
  - Login required (get_current_user) for view; full-user role required
    (require_full_user) for add/replace/delete — view-only users get a
    redirect-to-/menu via the NotFullUser handler.
  - The URL path takes a transaction_id (an integer); the actual on-disk
    filename (UUID-based) is looked up from the DB. Users can never
    influence which file is served beyond pointing at a transaction id.

Period locks: deliberately NOT enforced for add/replace/delete.
Attachments are documentation, not financial substance — see the
trigger refinement in migration 009.

Browsers: GET sets Content-Disposition: inline (so PDFs / images render
in-tab) and includes the original filename via RFC 6266 percent-encoded
filename* so saving keeps the friendly name.
"""

import mimetypes
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row

from app.auth import get_current_user, require_full_user
from app.config import settings
from app.db import get_connection
from app.services import attachments as attachments_service

router = APIRouter()


@router.get("/attachments/{transaction_id}")
def serve_attachment(
    transaction_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    # 1. Look up the stored filename + original name from the DB.
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT attachment_path, attachment_original_name
              FROM transactions
             WHERE id = %s
            """,
            (transaction_id,),
        )
        row = cur.fetchone()

    if row is None or not row["attachment_path"]:
        raise HTTPException(status_code=404, detail="No attachment for this transaction")

    # 2. Resolve the file on disk. attachment_path is a bare UUID-based
    #    filename (we never let user input flow into this), so it can't
    #    escape the upload dir — but we do a final containment check.
    file_path = (settings.upload_dir / row["attachment_path"]).resolve()
    upload_root = settings.upload_dir.resolve()
    try:
        file_path.relative_to(upload_root)
    except ValueError:
        # Defensive: should be impossible given how we generate filenames,
        # but never serve files outside the upload dir.
        raise HTTPException(status_code=404, detail="Attachment file missing on disk")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing on disk")

    # 3. Pick a Content-Type based on the original filename's extension so
    #    PDFs / images / text render in-browser rather than download.
    original_name = row["attachment_original_name"] or row["attachment_path"]
    media_type, _ = mimetypes.guess_type(original_name)
    if media_type is None:
        media_type = "application/octet-stream"

    # 4. inline disposition (display in-tab); RFC 6266 filename* keeps any
    #    non-ASCII chars from the original name intact when downloading.
    encoded_name = quote(original_name)
    content_disposition = f"inline; filename*=UTF-8''{encoded_name}"

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


# --- Add / Replace ---------------------------------------------------------

@router.post("/attachments/{transaction_id}")
async def upload_attachment(
    transaction_id: int,
    user: dict = Depends(require_full_user),
    file: Optional[UploadFile] = File(None),
):
    """
    Add a new attachment, or replace an existing one. Same operation either
    way — the service layer handles old-file cleanup. JSON response:
        { "ok": true, "transaction_id": int, "attachment_filename": str }
    """
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    file_bytes = await file.read()

    try:
        result = attachments_service.replace_attachment(
            transaction_id=transaction_id,
            file_bytes=file_bytes,
            original_name=file.filename,
        )
    except attachments_service.TransactionNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    except attachments_service.AttachmentTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return JSONResponse({"ok": True, **result})


# --- Delete ----------------------------------------------------------------

@router.post("/attachments/{transaction_id}/delete")
def delete_attachment(
    transaction_id: int,
    user: dict = Depends(require_full_user),
):
    """
    Remove the attachment from a transaction. JSON response:
        { "ok": true, "transaction_id": int, "attachment_filename": null }
    """
    try:
        result = attachments_service.delete_attachment(transaction_id)
    except attachments_service.TransactionNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    return JSONResponse({"ok": True, **result})
