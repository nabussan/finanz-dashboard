-- Migration 2026-06-27: Watchlist/PF/Micro-Cluster-Tabellen + micro_list kind
-- Anwenden auf bestehende Installationen:
--   PGPASSWORD=... psql -U finanz -h localhost -d finanz -f infra/migrate_2026_06_27_micro_pipe.sql

-- Cluster-Tabellen (Watchlists, Portfolio-Listen, Micro-Listen)
CREATE TABLE IF NOT EXISTS clusters (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    kind    TEXT NOT NULL,
    created TIMESTAMPTZ DEFAULT now(),
    UNIQUE (name, kind),
    CHECK (kind = ANY (ARRAY['watchlist', 'portfolio_list', 'micro_list']))
);
CREATE INDEX IF NOT EXISTS clusters_kind_idx ON clusters (kind);

CREATE TABLE IF NOT EXISTS cluster_items (
    cluster_id  INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    tv_symbol   TEXT NOT NULL,
    ticker      TEXT GENERATED ALWAYS AS (split_part(tv_symbol, ':', 2)) STORED,
    added       TIMESTAMPTZ DEFAULT now(),
    klasse      TEXT,
    benchmark   TEXT DEFAULT 'AMEX:SPY',
    notizen     TEXT,
    ibkr_status TEXT DEFAULT 'resolved',
    PRIMARY KEY (cluster_id, tv_symbol)
);
CREATE INDEX IF NOT EXISTS cluster_items_ticker_idx ON cluster_items (ticker);

-- Falls Tabellen schon existieren: micro_list zum CHECK-Constraint hinzufügen
DO $$
BEGIN
    -- CHECK-Constraint erneuern falls micro_list noch fehlt
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'clusters_kind_check'
        AND NOT (pg_get_constraintdef(oid) LIKE '%micro_list%')
    ) THEN
        ALTER TABLE clusters DROP CONSTRAINT clusters_kind_check;
        ALTER TABLE clusters ADD CONSTRAINT clusters_kind_check
            CHECK (kind = ANY (ARRAY['watchlist', 'portfolio_list', 'micro_list']));
    END IF;

    -- ibkr_status-Spalte ergänzen falls sie fehlt
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cluster_items' AND column_name = 'ibkr_status'
    ) THEN
        ALTER TABLE cluster_items ADD COLUMN ibkr_status TEXT DEFAULT 'resolved';
    END IF;

    -- sec_type-Spalte ergänzen falls sie fehlt (ETF/Fonds-Klassifikation für Pre-Filter)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cluster_items' AND column_name = 'sec_type'
    ) THEN
        ALTER TABLE cluster_items ADD COLUMN sec_type TEXT;
    END IF;
END$$;
