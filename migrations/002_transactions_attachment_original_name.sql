-- =====================================================================
-- Phase 6: track the original filename of uploaded attachments separately
-- from the on-disk path.
--
-- transactions.attachment_path stores the UUID-based filename we use on
-- disk (so collisions and path-traversal can't happen).
-- transactions.attachment_original_name stores the user-supplied filename
-- so we can show it in reports / future view-transaction screens.
-- =====================================================================

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS attachment_original_name TEXT;
