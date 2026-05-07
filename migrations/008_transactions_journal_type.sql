-- =====================================================================
-- Add a journal_type column to transactions to distinguish manual
-- entries from system-generated closing journals.
--
-- Values:
--   'STANDARD'        — every regular journal entry posted via the
--                       Add Transaction screen
--   'YEAR_END_CLOSE'  — system-generated closing journal posted by the
--                       Year-End Close feature; references YEC-YYYY,
--                       zeroes P&L accounts into 3100 Retained Earnings
--
-- All existing rows backfill to 'STANDARD' via the DEFAULT.
-- =====================================================================

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS journal_type TEXT NOT NULL DEFAULT 'STANDARD';

-- ADD CONSTRAINT doesn't support IF NOT EXISTS in PostgreSQL, so guard
-- with a DO block that checks pg_constraint first. Keeps the migration
-- safely re-runnable even if the runner's bookkeeping is wrong.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'transactions_journal_type_check'
    ) THEN
        ALTER TABLE transactions
            ADD CONSTRAINT transactions_journal_type_check
            CHECK (journal_type IN ('STANDARD', 'YEAR_END_CLOSE'));
    END IF;
END $$;

-- Partial index — STANDARD is the bulk; closing journals are rare. The
-- partial WHERE keeps the index small and lookups for non-standard
-- entries fast (used by the year-end-close pre-flight check).
CREATE INDEX IF NOT EXISTS idx_transactions_journal_type
    ON transactions (journal_type)
    WHERE journal_type <> 'STANDARD';
