-- Migration: Sub-Score-Spalten zur fundamentals-Tabelle hinzufügen
-- Anwendung: psql -U finanz -d finanz -f migrate_add_sub_scores.sql

ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_trends        NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_cashflow      NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_profitability NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_valuation     NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_liquidity     NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS score_solvency      NUMERIC;
