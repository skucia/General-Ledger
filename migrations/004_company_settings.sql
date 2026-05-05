-- =====================================================================
-- company_settings: a singleton-row table holding global app settings
-- (just company_name for now; we'll add more in a future settings-page
-- session). The CHECK (id = 1) plus PK guarantees there can only ever be
-- one row, so SELECT company_name FROM company_settings WHERE id = 1 is
-- always the right query.
-- =====================================================================

CREATE TABLE IF NOT EXISTS company_settings (
    id            INTEGER PRIMARY KEY DEFAULT 1,
    company_name  TEXT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT company_settings_singleton CHECK (id = 1)
);

-- Seed the row on first run; ON CONFLICT keeps the migration idempotent.
INSERT INTO company_settings (id, company_name)
VALUES (1, 'Kasis Sdn Bhd')
ON CONFLICT (id) DO NOTHING;
