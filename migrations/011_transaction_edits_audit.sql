-- =====================================================================
-- Phase 8: edit posted transactions (current/open period only).
--
-- Transactions were append-only until now — corrections were made by
-- reversal. We now allow in-place editing of STANDARD transactions in the
-- OPEN period (date after the last period lock). To keep the ledger
-- defensible, every edit captures a full before-snapshot of the
-- transaction (header + lines) into this audit table, written in the SAME
-- DB transaction as the edit.
--
-- No trigger or column changes are needed elsewhere: the period-lock
-- trigger already permits UPDATEs to open-period rows and blocks locked
-- ones, and the deferred balance trigger re-checks DR=CR at COMMIT.
--
-- before_snapshot JSONB shape:
--   {
--     "transaction_date": "YYYY-MM-DD",
--     "description": "...",
--     "transaction_reference": "...",
--     "journal_type": "STANDARD",
--     "lines": [ {"dr_cr": "DR", "account_number": "1000", "amount": "10.00"}, ... ]
--   }
-- =====================================================================

CREATE TABLE IF NOT EXISTS transaction_edits (
    id               SERIAL PRIMARY KEY,
    transaction_id   INTEGER NOT NULL
                         REFERENCES transactions(id) ON DELETE CASCADE,
    edited_by        INTEGER NOT NULL REFERENCES users(id),
    edited_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason           TEXT,
    before_snapshot  JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transaction_edits_txn
    ON transaction_edits(transaction_id);
