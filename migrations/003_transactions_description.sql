-- =====================================================================
-- Phase 6 (mid-phase fix): add a header-level Description field.
--
-- Required, max 200 chars, displayed alongside Transaction Date on the
-- Add Transaction form. Three steps so the migration is safe even if
-- some transaction rows already exist (we add it nullable, backfill any
-- nulls with a clear placeholder, then enforce NOT NULL).
-- =====================================================================

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS description VARCHAR(200);

-- Idempotent backfill: only rows still NULL get the placeholder.
UPDATE transactions
   SET description = '(no description)'
 WHERE description IS NULL;

ALTER TABLE transactions
    ALTER COLUMN description SET NOT NULL;
