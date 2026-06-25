-- =====================================================================
-- Phase 7: multiple attachments per transaction.
--
-- Until now a transaction carried at most ONE attachment, stored as two
-- columns directly on the row (attachment_path + attachment_original_name).
-- This migration moves attachments into a child table so a transaction
-- can have many (the app caps it at 5; see app/services/attachments.py).
--
--   transaction_attachments.attachment_path          — UUID-based on-disk
--       filename (collision- and path-traversal-safe).
--   transaction_attachments.attachment_original_name — user-supplied name,
--       shown in the UI / reports.
--
-- Existing single attachments are migrated into the new table, then the
-- old columns are dropped. ON DELETE CASCADE means deleting a transaction
-- cleans up its attachment ROWS (the files on disk are unlinked by the
-- app, not the DB).
--
-- No period-lock trigger is placed on this table: attachments are
-- documentation, not financial substance, so they stay editable even on
-- locked-period transactions. (Writes no longer touch the transactions
-- table at all, so the migration-009 attachment-bypass branch on that
-- trigger becomes dead-but-harmless — left intact.)
-- =====================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS transaction_attachments (
    id                       SERIAL PRIMARY KEY,
    transaction_id           INTEGER NOT NULL
                                 REFERENCES transactions(id) ON DELETE CASCADE,
    attachment_path          TEXT NOT NULL,
    attachment_original_name TEXT NOT NULL,
    uploaded_by              INTEGER REFERENCES users(id),
    uploaded_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transaction_attachments_txn
    ON transaction_attachments(transaction_id);

-- Migrate existing single attachments into the new table. COALESCE guards
-- any legacy row that has a path but a NULL original name.
INSERT INTO transaction_attachments
    (transaction_id, attachment_path, attachment_original_name)
SELECT id, attachment_path, COALESCE(attachment_original_name, attachment_path)
  FROM transactions
 WHERE attachment_path IS NOT NULL;

-- Drop the now-migrated single-attachment columns.
ALTER TABLE transactions DROP COLUMN IF EXISTS attachment_path;
ALTER TABLE transactions DROP COLUMN IF EXISTS attachment_original_name;

COMMIT;
