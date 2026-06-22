from pathlib import Path
from dotenv import load_dotenv
import os

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

DISCO_LATEST_HTML = DISCO_OUTPUT_DIR / "discovery_latest.html"
RSM_PORTFOLIO_HTML       = RSM_DATA_DIR / "charts" / "portfolio.html"
RSM_PORTFOLIO_DAILY_HTML = RSM_DATA_DIR / "charts" / "portfolio_daily.html"
