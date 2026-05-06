-- =====================================================================
-- Period locking infrastructure.
--
-- Adds a period_locks table that records which periods are closed
-- (no further writes allowed for transactions on or before
-- locked_through), plus a BEFORE INSERT/UPDATE/DELETE trigger on
-- transactions that enforces the lock at the database level.
--
-- Belt-and-braces with the application layer: app/services/transactions.py
-- calls _assert_period_open() before every write to give a friendly
-- user-facing error in normal flow. This DB trigger is the safety net
-- against direct SQL writes and any future write paths that forget to
-- call the app-layer check.
-- =====================================================================

CREATE TABLE IF NOT EXISTS period_locks (
    id              SERIAL PRIMARY KEY,
    locked_through  DATE NOT NULL,
    locked_by       INTEGER NOT NULL REFERENCES users(id),
    locked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason          TEXT
);

-- Multiple lock rows allowed (one per close event — gives an audit
-- trail of who locked what and when). The current effective lock is
-- always MAX(locked_through), which the DESC index supports.
CREATE INDEX IF NOT EXISTS idx_period_locks_locked_through
    ON period_locks (locked_through DESC);

-- ---------------------------------------------------------------------
-- Trigger function: reject any write to transactions whose
-- transaction_date falls in a locked period.
--
-- Custom SQLSTATE 'GL001' so the application layer can distinguish a
-- period-lock failure from the existing DR=CR balance-trigger failure
-- (which raises with default SQLSTATE P0001).
--
-- Behaviour by operation:
--   INSERT: NEW.transaction_date must be > lock_date
--   UPDATE: BOTH NEW and OLD transaction_date must be > lock_date
--           (can't move a locked-period txn out, and the new date
--           must itself land in an open period)
--   DELETE: OLD.transaction_date must be > lock_date
-- ---------------------------------------------------------------------

CREATE OR REPLACE FUNCTION check_transaction_period_lock()
RETURNS TRIGGER AS $$
DECLARE
    lock_date DATE;
BEGIN
    SELECT MAX(locked_through) INTO lock_date FROM period_locks;

    -- No lock — anything goes.
    IF lock_date IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- INSERT or UPDATE: the new date must be in an open period.
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.transaction_date <= lock_date THEN
        RAISE EXCEPTION
            'Transaction date % is in a locked period (locked through %)',
            NEW.transaction_date, lock_date
            USING ERRCODE = 'GL001';
    END IF;

    -- UPDATE or DELETE: the old date must be in an open period.
    -- (Stops a transaction from being edited or deleted out of a locked period.)
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.transaction_date <= lock_date THEN
        RAISE EXCEPTION
            'Transaction date % is in a locked period (locked through %)',
            OLD.transaction_date, lock_date
            USING ERRCODE = 'GL001';
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_transaction_period_lock ON transactions;

CREATE TRIGGER trg_check_transaction_period_lock
    BEFORE INSERT OR UPDATE OR DELETE
    ON transactions
    FOR EACH ROW
    EXECUTE FUNCTION check_transaction_period_lock();
