-- Migration 2026-06-14: Neue Spalten fundamentals + prices-Tabelle
-- Anwenden auf bestehende Installationen:
--   PGPASSWORD=... psql -U finanz -h localhost -d finanz_live -f infra/migrate_2026_06_14.sql

DO $$ BEGIN ASSERT current_database() = 'finanz_live', 'Falsche DB! Erwartet: finanz_live'; END $$;

ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS exchange TEXT NOT NULL DEFAULT '';
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS roic     NUMERIC;

CREATE TABLE IF NOT EXISTS prices (
    ticker   TEXT    NOT NULL,
    exchange TEXT    NOT NULL DEFAULT '',
    date     DATE    NOT NULL,
    open     NUMERIC,
    high     NUMERIC,
    low      NUMERIC,
    close    NUMERIC,
    volume   BIGINT,
    PRIMARY KEY (ticker, exchange, date)
);
CREATE INDEX IF NOT EXISTS prices_ticker_date_idx ON prices (ticker, exchange, date DESC);

-- Nach Migration: DB neu befüllen
--   source .venv/bin/activate && python scripts/import_json_to_db.py
