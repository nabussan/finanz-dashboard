-- Migration: Sub-Score-Spalten zur fundamentals-Tabelle hinzufügen
-- Anwendung: psql -U finanz -d finanz_live -f migrate_add_sub_scores.sql

DO $$ BEGIN ASSERT current_database() = 'finanz_live', 'Falsche DB! Erwartet: finanz_live'; END $$;

ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_trends        NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_cashflow      NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_profitability NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_valuation     NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_liquidity     NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_solvency      NUMERIC;
