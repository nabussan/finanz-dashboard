from pathlib import Path
from dotenv import load_dotenv
import os
import yaml

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / ".env")

DB_URL: str = os.environ.get("DB_URL", "")
PORT: int = int(os.environ.get("DASHBOARD_PORT", "8080"))

# Gepaartes IBKR-Gateway-LXC (fuer Status-Karte + Re-Login-Button)
GATEWAY_HOST: str        = os.environ.get("GATEWAY_HOST", "")
GATEWAY_LXC_ID: str       = os.environ.get("GATEWAY_LXC_ID", "")
GATEWAY_RESTART_KEY: str = os.environ.get("GATEWAY_RESTART_KEY", "")
GATEWAY_SCREENSHOT_KEY: str = os.environ.get("GATEWAY_SCREENSHOT_KEY", "")
GATEWAY_RECONNECT_KEY: str = os.environ.get("GATEWAY_RECONNECT_KEY", "")
GATEWAY_STOP_KEY: str    = os.environ.get("GATEWAY_STOP_KEY", "")

# Pfade zu generierten Artefakten der Quellsysteme
DISCO_OUTPUT_DIR = Path(os.environ.get("DISCO_OUTPUT_DIR", _REPO_ROOT / "disco/output"))
RSM_DATA_DIR     = Path(os.environ.get("RSM_DATA_DIR",     _REPO_ROOT / "rsm-live/data"))
MICRO_JSON_DIR    = Path(os.environ.get("MICRO_JSON_DIR",    "/home/christoph/Finanz/micro/data/json"))
MICRO_CLUSTER_DIR = Path(os.environ.get("MICRO_CLUSTER_DIR", Path(__file__).parent / "data" / "micro_clusters"))
MICRO_CONFIG_PATH = Path(os.environ.get("MICRO_CONFIG_PATH", "/home/christoph/Finanz/micro/config/config_kennzahlen.txt"))
MICRO_SCRAPER_PATH   = Path(os.environ.get("MICRO_SCRAPER_PATH",   "/home/christoph/Finanz/rsm-live/src/fetch_fundamentals.py"))
MICRO_SCRAPER_PYTHON = Path(os.environ.get("MICRO_SCRAPER_PYTHON", "/home/christoph/Finanz/rsm-live/.venv/bin/python"))

DISCO_UNIVERSE_PATH = Path(os.environ.get(
    "DISCO_UNIVERSE_PATH",
    str(Path(__file__).parent / "config" / "universe.yaml"),
))

def load_ucits_map() -> dict[str, str]:
    """Liefert {US-ticker → UCITS-Symbol}, z.B. {'SPY': 'LSE:CSPX'}."""
    try:
        data = yaml.safe_load(DISCO_UNIVERSE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[str, str] = {}
    for entries in data.values():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and e.get("ucits") and e.get("ticker"):
                result.setdefault(e["ticker"], e["ucits"])
    return result

UCITS_MAP: dict[str, str] = load_ucits_map()

DISCO_LATEST_HTML = DISCO_OUTPUT_DIR / "discovery_latest.html"
RSM_PORTFOLIO_HTML       = RSM_DATA_DIR / "charts" / "portfolio.html"
RSM_PORTFOLIO_DAILY_HTML = RSM_DATA_DIR / "charts" / "portfolio_daily.html"

# Ampel-Schwellenwerte für den /micro-View
# hi=True: höher=besser (Grün wenn >= green), hi=False: niedriger=besser (Grün wenn <= green)
CRON_JOBS: list[dict] = [
    {"label": "Intraday-Kurse",          "expr": "*/15 9-21 * * 1-5",  "task": "intraday", "desc": "intraday_cron.sh — Mo–Fr 09–22 Uhr alle 15 Min"},
    {"label": "OHLCV-Preisdaten",        "expr": "45 22 * * 1-5",      "task": "eod",      "desc": "eod_update_cron.sh — Mo–Fr 22:45 (enthält IV-Daten)"},
    {"label": "W3-Scores + Charts",      "expr": "15 23 * * 1-5",      "task": "scores",   "desc": "run_w3_cron.sh — Mo–Fr 23:15 (enthält Charts-Regenerierung)"},
    {"label": "Klasse (Full-Reclassify)","expr": "0 6 1 1,4,7,10 *",   "task": "classify", "desc": "determine_class.py — 1. Jan/Apr/Jul/Okt 06:00"},
    {"label": "disco Sync",              "expr": "30 8 * * 1",          "task": None,       "desc": "disco_sync_cron.sh — Mo 08:30"},
    {"label": "Gateway-Health",          "expr": "*/1 * * * *",         "task": None,       "desc": "check_gateway.py — jede Minute"},
]

MICRO_AMPEL: dict = {
    "score":    {"hi": True,  "green": 0.55, "orange": 0.35},
    "trends":   {"hi": True,  "green": 0.55, "orange": 0.35},
    "cf":       {"hi": True,  "green": 0.55, "orange": 0.35},
    "profit":   {"hi": True,  "green": 0.55, "orange": 0.35},
    "roic":     {"hi": True,  "green": 15.0, "orange":  8.0},
    "evebitda": {"hi": False, "green": 12.0, "orange": 20.0},
    "roe":      {"hi": True,  "green": 15.0, "orange":  8.0},
    "de":       {"hi": False, "green":  0.5, "orange":  1.5},
}
