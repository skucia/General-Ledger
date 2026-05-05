-- =====================================================================
-- Add a header-level Transaction Reference field.
--
-- VARCHAR(20), NOT NULL with DEFAULT '' so:
--   - existing rows automatically get '' (no separate backfill step)
--   - bulk imports / direct SQL inserts that omit the column still work
--   - the application layer enforces non-blank for new user submissions
--     (validates and trims in app/routers/transactions.py)
--
-- No uniqueness constraint — multiple transactions may share a reference.
-- =====================================================================

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS transaction_reference VARCHAR(20) NOT NULL DEFAULT '';
