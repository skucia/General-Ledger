"""
Serve transaction attachments for in-browser viewing.

Security:
  - Login required (any logged-in user may view attachments).
  - The URL path takes a transaction_id (an integer); the actual on-disk
    filename (UUID-based) is looked up from the DB. Users can never
    influence which file is served beyond pointing at a transaction id.
  - 404 if the transaction does not exist OR has no attachment OR the
    file is missing on disk.

Browsers: we set Content-Disposition: inline (so PDFs / images render
in-tab) and include the original filename via RFC 6266 percent-encoded
filename* so saving keeps the friendly name.
"""

import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from psycopg.rows import dict_row

from app.auth import get_current_user
from app.config import settings
from app.db import get_connection

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
