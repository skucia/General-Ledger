-- =====================================================================
-- Period-lock trigger refinement: allow attachment-only UPDATEs on
-- locked-period transactions.
--
-- Background: the trigger from migration 006 fires on every INSERT /
-- UPDATE / DELETE on transactions and rejects writes against locked
-- periods. That's the right behaviour for financial substance, but
-- attachments are documentation — adding/replacing/deleting an
-- attachment on a locked-period transaction is intentionally allowed
-- by the app (see app/routers/attachments.py + app/services/attachments.py).
--
-- This migration replaces the trigger function with one that adds an
-- early-return branch: if an UPDATE doesn't change any financial
-- column, skip the lock check. INSERT, DELETE, and any UPDATE that
-- touches a financial column behave exactly as before.
--
-- Identifying "attachment-only" by "no financial column changed" is
-- more robust than "only attachment_* changed" — adding a new metadata
-- column later won't accidentally re-introduce the lock for it.
-- =====================================================================

CREATE OR REPLACE FUNCTION check_transaction_period_lock()
RETURNS TRIGGER AS $$
DECLARE
    lock_date DATE;
BEGIN
    -- Attachment-only UPDATEs bypass the lock entirely. Detected by
    -- "no financial column changed" — see migration header for rationale.
    IF TG_OP = 'UPDATE'
       AND NEW.transaction_date IS NOT DISTINCT FROM OLD.transaction_date
       AND NEW.description IS NOT DISTINCT FROM OLD.description
       AND NEW.transaction_reference IS NOT DISTINCT FROM OLD.transaction_reference
       AND NEW.created_by IS NOT DISTINCT FROM OLD.created_by
       AND NEW.journal_type IS NOT DISTINCT FROM OLD.journal_type
       AND NEW.reverses_transaction_id IS NOT DISTINCT FROM OLD.reverses_transaction_id
    THEN
        RETURN NEW;
    END IF;

    SELECT MAX(locked_through) INTO lock_date FROM period_locks;

    -- No lock — anything goes.
    IF lock_date IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- INSERT or financial UPDATE: the new date must be in an open period.
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.transaction_date <= lock_date THEN
        RAISE EXCEPTION
            'Transaction date % is in a locked period (locked through %)',
            NEW.transaction_date, lock_date
            USING ERRCODE = 'GL001';
    END IF;

    -- Financial UPDATE or DELETE: the old date must be in an open period.
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.transaction_date <= lock_date THEN
        RAISE EXCEPTION
            'Transaction date % is in a locked period (locked through %)',
            OLD.transaction_date, lock_date
            USING ERRCODE = 'GL001';
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
