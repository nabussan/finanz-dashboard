#!/usr/bin/env python3
"""
Initiale DB-Befüllung: Alle TV-JSON-Dateien → fundamentals-Tabelle.

Aufruf:
  cd /home/christoph/Finanz/finanz-dashboard
  .venv/bin/python scripts/import_json_to_db.py

Setzt DB_URL (entweder als Env-Var oder in .env).
"""
import sys
from pathlib import Path

# Dashboard-Root zum Pfad hinzufügen
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from routers.micro_score import score_tickers, write_to_db_sync

if not config.DB_URL:
    print("FEHLER: DB_URL nicht gesetzt (Env-Var oder .env)")
    sys.exit(1)

if not config.MICRO_JSON_DIR.exists():
    print(f"FEHLER: MICRO_JSON_DIR nicht gefunden: {config.MICRO_JSON_DIR}")
    sys.exit(1)

print(f"Scoring über alle JSON-Dateien in: {config.MICRO_JSON_DIR}")
print(f"Config: {config.MICRO_CONFIG_PATH}")

results = score_tickers([], config.MICRO_JSON_DIR, config.MICRO_CONFIG_PATH)
print(f"Scoring abgeschlossen: {len(results)} Ticker")

n = write_to_db_sync(results, config.DB_URL)
print(f"→ {n} Ticker in fundamentals geschrieben (source='tv')")
