-- Migration: clusters.kind → cluster_views Join-Tabelle
-- Vorher prüfen: SELECT name, COUNT(*) FROM clusters GROUP BY name HAVING COUNT(*) > 1;
-- Duplikate 2026-06-27: XLI (micro+watchlist), Aktien-US_1c459 (micro+watchlist), Optionen (micro+portfolio)

BEGIN;

-- 1. Join-Tabelle anlegen
CREATE TABLE IF NOT EXISTS cluster_views (
    cluster_id  INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    view_name   TEXT    NOT NULL CHECK (view_name = ANY (ARRAY['watchlist', 'micro', 'portfolio'])),
    assigned_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (cluster_id, view_name)
);
CREATE INDEX IF NOT EXISTS cluster_views_view_name_idx ON cluster_views (view_name);

-- 2. Namens-Duplikate auflösen (gleicher Name, verschiedene kinds → Suffix anfügen)
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT id, name, kind FROM clusters
        WHERE name IN (SELECT name FROM clusters GROUP BY name HAVING COUNT(DISTINCT kind) > 1)
        ORDER BY name, kind
    LOOP
        UPDATE clusters SET name = r.name || ' (' ||
            CASE r.kind WHEN 'watchlist'      THEN 'watchlist'
                        WHEN 'portfolio_list' THEN 'portfolio'
                        WHEN 'micro_list'     THEN 'micro' END || ')'
        WHERE id = r.id;
    END LOOP;
END$$;

-- 3. Bestandsdaten übertragen (nach Umbenennung, damit keine Konflikte)
INSERT INTO cluster_views (cluster_id, view_name)
SELECT id,
    CASE kind
        WHEN 'watchlist'      THEN 'watchlist'
        WHEN 'portfolio_list' THEN 'portfolio'
        WHEN 'micro_list'     THEN 'micro'
    END
FROM clusters
ON CONFLICT DO NOTHING;

-- 4. UNIQUE-Constraint + kind-Spalte entfernen
ALTER TABLE clusters DROP CONSTRAINT IF EXISTS clusters_name_kind_key;
ALTER TABLE clusters DROP CONSTRAINT IF EXISTS "clusters_name_kind_key";
ALTER TABLE clusters DROP CONSTRAINT IF EXISTS clusters_kind_check;
DROP INDEX IF EXISTS clusters_kind_idx;
ALTER TABLE clusters ADD CONSTRAINT clusters_name_unique UNIQUE (name);
ALTER TABLE clusters DROP COLUMN kind;

COMMIT;
