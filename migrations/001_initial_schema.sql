-- =====================================================================
-- General Ledger — initial schema
-- Tables: users, accounts, transactions, transaction_lines
-- Enforces DR=CR per transaction via a deferrable constraint trigger.
-- All statements use IF NOT EXISTS / CREATE OR REPLACE so the migration
-- can be re-run safely.
-- =====================================================================

-- ---------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                    SERIAL PRIMARY KEY,
    username              TEXT NOT NULL UNIQUE,
    email                 TEXT NOT NULL,
    password_hash         TEXT NOT NULL,
    user_type             TEXT NOT NULL CHECK (user_type IN ('full', 'view')),
    is_admin              BOOLEAN NOT NULL DEFAULT FALSE,
    must_change_password  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------
-- accounts
-- account_number is the natural primary key (alphanumeric, max 20 chars).
-- account_type uses single-letter codes: S=Sales, C=Costs, A=Asset, L=Liability, E=Equity.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    account_number  VARCHAR(20) PRIMARY KEY
                    CHECK (account_number ~ '^[A-Za-z0-9]+$'),
    account_name    VARCHAR(30) NOT NULL,
    account_type    CHAR(1) NOT NULL CHECK (account_type IN ('S', 'C', 'A', 'L', 'E')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------
-- transactions (header)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id                SERIAL PRIMARY KEY,
    transaction_date  DATE NOT NULL,
    attachment_path   TEXT,                 -- nullable: attachment is optional
    created_by        INTEGER NOT NULL REFERENCES users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------
-- transaction_lines (detail)
-- amount must be strictly positive; the DR/CR flag indicates the side.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_lines (
    id              SERIAL PRIMARY KEY,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    dr_cr           CHAR(2) NOT NULL CHECK (dr_cr IN ('DR', 'CR')),
    account_number  VARCHAR(20) NOT NULL REFERENCES accounts(account_number),
    amount          NUMERIC(18, 2) NOT NULL CHECK (amount > 0)
);

CREATE INDEX IF NOT EXISTS idx_transaction_lines_txn
    ON transaction_lines (transaction_id);

CREATE INDEX IF NOT EXISTS idx_transaction_lines_account
    ON transaction_lines (account_number);

-- ---------------------------------------------------------------------
-- DR=CR enforcement
--
-- A normal CHECK constraint can only see one row at a time, so it can't
-- compare SUM(DR) vs SUM(CR). Instead we use a CONSTRAINT TRIGGER that
-- runs at COMMIT time (DEFERRABLE INITIALLY DEFERRED). This way the app
-- can INSERT the header, then INSERT N lines inside one DB transaction,
-- and the balance check fires once at the end.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_transaction_balanced()
RETURNS TRIGGER AS $$
DECLARE
    txn_id INTEGER;
    dr_total NUMERIC(18, 2);
    cr_total NUMERIC(18, 2);
BEGIN
    -- On DELETE, OLD has the row; otherwise NEW does.
    IF TG_OP = 'DELETE' THEN
        txn_id := OLD.transaction_id;
    ELSE
        txn_id := NEW.transaction_id;
    END IF;

    SELECT
        COALESCE(SUM(CASE WHEN dr_cr = 'DR' THEN amount END), 0),
        COALESCE(SUM(CASE WHEN dr_cr = 'CR' THEN amount END), 0)
      INTO dr_total, cr_total
      FROM transaction_lines
     WHERE transaction_id = txn_id;

    -- A transaction with zero lines (e.g. all lines deleted) is allowed
    -- so that ON DELETE CASCADE from transactions still works cleanly.
    IF (dr_total + cr_total) > 0 AND dr_total <> cr_total THEN
        RAISE EXCEPTION
            'Transaction % is not balanced: DR=% CR=%',
            txn_id, dr_total, cr_total;
    END IF;

    RETURN NULL;  -- return value is ignored for AFTER triggers
END;
$$ LANGUAGE plpgsql;

-- Drop and recreate the trigger so re-running the migration is safe.
DROP TRIGGER IF EXISTS trg_check_transaction_balanced ON transaction_lines;

CREATE CONSTRAINT TRIGGER trg_check_transaction_balanced
    AFTER INSERT OR UPDATE OR DELETE
    ON transaction_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION check_transaction_balanced();
