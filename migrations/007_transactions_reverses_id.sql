-- =====================================================================
-- Self-referencing FK on transactions to track reversal pairs.
--
-- When transaction B reverses transaction A, B.reverses_transaction_id
-- = A.id. Enables audit queries ("show all reversals", "has this txn
-- been reversed?") and prevents reversal-link rot when reports later
-- need to surface this relationship.
--
-- Nullable: most transactions don't reverse anything, so the column
-- is NULL by default.
--
-- ON DELETE SET NULL: deleting an original (only possible if it's in
-- an open period — period_locks blocks deletes in closed periods)
-- un-links its reversal rather than cascading the delete. The
-- reversal stays as a standalone entry with reverses_transaction_id
-- = NULL.
-- =====================================================================

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS reverses_transaction_id INTEGER
    REFERENCES transactions(id) ON DELETE SET NULL;

-- Partial index: most rows are NULL (most txns aren't reversals), so
-- skip those entries to keep the index small.
CREATE INDEX IF NOT EXISTS idx_transactions_reverses
    ON transactions (reverses_transaction_id)
    WHERE reverses_transaction_id IS NOT NULL;
