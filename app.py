"""Finanz Dashboard — FastAPI entry point."""
import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import config
from routers import disco, rsm, portfolio, micro, watchlist, portfolio_lists

RSM_DIR = Path(__file__).parent.parent / "rsm-live"

_PIPELINE_TASKS = {
    "intraday": ("Intraday-Kurse (Snapshot)", ["src/intraday_prices.py"]),
    "eod":    ("EOD-Update (OHLCV)",          ["src/eod_update.py", "--skip-ibkr"]),  # --skip-ibkr = kein Options-Screen
    "iv":     ("IV-Daten (IBKR Options)",      ["src/run_w3.py", "--ibkr-only"]),
    "scores": ("W3-Scores",                    ["src/run_w3.py", "--skip-update", "--skip-notify"]),
    "charts": ("Charts neu generieren",        ["src/make_charts.py"]),
    "full":   ("Alles (EOD + IV + Scores + Charts)", None),  # handled separately
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_pool()
    yield
    await db.close_pool()


app = FastAPI(title="Finanz Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.middleware("http")
async def inject_price_updates(request: Request, call_next):
    """Letzten Intraday/EOD-Timestamp in request.state injizieren (für layout.html)."""
    try:
        pool = await db.get_pool()
        request.state.price_updates = await db.get_price_update_times(pool)
    except Exception:
        request.state.price_updates = {}
    return await call_next(request)


def _score_class(v):
    if v is None: return ""
    return "ticker-stat--green" if v >= 70 else "ticker-stat--orange" if v >= 40 else "ticker-stat--red"

def _ivr_class(v):
    if v is None: return ""
    return "ticker-stat--green" if v > 50 else "ticker-stat--orange" if v > 20 else ""

def _vrp_class(v):
    if v is None: return ""
    return "ticker-stat--green" if v > 1.3 else "ticker-stat--orange" if v > 0.8 else ""

def _rend_class(v):
    if v is None: return ""
    return "ticker-stat--green" if v > 20 else "ticker-stat--orange" if v > 10 else "ticker-stat--red" if v < 0 else ""

templates.env.globals.update(
    score_class=_score_class,
    ivr_class=_ivr_class,
    vrp_class=_vrp_class,
    rend_class=_rend_class,
)

app.include_router(disco.router)
app.include_router(rsm.router)
app.include_router(portfolio.router)
app.include_router(micro.router)
app.include_router(watchlist.router)
app.include_router(portfolio_lists.router)


async def _system_status(pool) -> list[dict]:
    """Letzter Update-Timestamp für jedes System — für Index-Karten."""
    systems = []

    queries = {
        "RSM Signale":   ("SELECT MAX(run_date)::TEXT FROM signals",       7),
        "Portfolios":    ("SELECT MAX(updated)::TEXT FROM positions",       1),
        "disco RRG":     ("SELECT MAX(week)::TEXT FROM rrg_data",           8),
        "Micro":         ("SELECT MAX(updated)::TEXT FROM fundamentals",   14),
    }
    urls = {
        "RSM Signale": "/rsm",
        "Portfolios":  "/portfolios",
        "disco RRG":   "/disco",
        "Micro":       "/micro",
    }

    from datetime import date, timedelta

    for name, (q, warn_days) in queries.items():
        try:
            val = await pool.fetchval(q)
            if val:
                last = date.fromisoformat(val[:10])
                age = (date.today() - last).days
                status = "green" if age <= warn_days else ("orange" if age <= warn_days * 2 else "red")
                label = f"vor {age}d" if age > 0 else "heute"
            else:
                status, label = "red", "kein Eintrag"
        except Exception:
            status, label = "grey", "DB-Fehler"

        systems.append({"name": name, "status": status, "label": label, "url": urls[name]})

    return systems


def _gateway_status() -> dict:
    """IBKR-Gateway-Karte: liest gateway_state.json (Cron check_gateway.py)."""
    from datetime import datetime

    name = "IBKR Gateway"
    check_now = {"url": "/admin/gateway/check-now", "label": "Jetzt prüfen"}
    links = [{"url": "/admin/gateway/screen", "label": "Gateway-Screen anzeigen"}] \
        if config.GATEWAY_SCREENSHOT_KEY else []

    if not config.GATEWAY_HOST:
        return {"name": name, "status": "grey", "label": "nicht konfiguriert", "url": None,
                "actions": [], "links": []}

    state = {}
    state_file = config.RSM_DATA_DIR / "gateway_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = {}

    age_min = None
    if state.get("checked_at"):
        try:
            age_min = (datetime.now() - datetime.fromisoformat(state["checked_at"])).total_seconds() / 60
        except Exception:
            age_min = None

    if age_min is None or age_min > 20:
        label = "Health-Check nie gelaufen" if age_min is None else f"Health-Check seit {int(age_min)}min inaktiv"
        return {"name": name, "status": "grey", "label": label, "url": None,
                "actions": [check_now], "links": links}

    if state.get("status") == "up":
        return {"name": name, "status": "green", "label": f"verbunden (vor {int(age_min)}min geprüft)",
                "url": None, "actions": [], "links": links}

    return {
        "name": name, "status": "red", "label": "nicht verbunden — Re-Login nötig", "url": None,
        "actions": [{"url": "/admin/gateway/restart", "label": "Re-Login starten"}, check_now],
        "links": links,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    pool = await db.get_pool()
    systems = await _system_status(pool)
    systems.append(_gateway_status())
    return templates.TemplateResponse(request, "index.html", {"systems": systems})


@app.post("/admin/run/{task}")
async def admin_run(task: str):
    if task not in _PIPELINE_TASKS:
        return HTMLResponse("Unbekannte Task", status_code=400)

    venv_python = str(RSM_DIR / ".venv" / "bin" / "python3")

    if task == "full":
        script = str(RSM_DIR / "infra" / "run_w3_cron.sh")
        subprocess.Popen(["bash", script], cwd=str(RSM_DIR))
    else:
        _, args = _PIPELINE_TASKS[task]
        subprocess.Popen([venv_python] + args, cwd=str(RSM_DIR))

    label = _PIPELINE_TASKS[task][0]
    return RedirectResponse(f"/?triggered={label}", status_code=303)


@app.post("/admin/gateway/restart")
async def gateway_restart():
    """Loest per SSH (Forced-Command-Key) einen Neustart des IBKR-Gateways aus.

    Welches Kommando tatsaechlich laeuft, bestimmt die authorized_keys-Zeile
    auf dem Gateway-LXC (command="systemctl restart ibgateway") — was hier
    als Argument uebergeben wird, ist irrelevant.
    """
    if config.GATEWAY_HOST and config.GATEWAY_RESTART_KEY:
        subprocess.Popen([
            "ssh", "-i", config.GATEWAY_RESTART_KEY,
            "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
            f"root@{config.GATEWAY_HOST}", "restart",
        ])
    return RedirectResponse("/?triggered=Gateway-Re-Login+gestartet", status_code=303)


@app.post("/admin/gateway/check-now")
async def gateway_check_now():
    venv_python = str(RSM_DIR / ".venv" / "bin" / "python3")
    subprocess.Popen([venv_python, "src/check_gateway.py"], cwd=str(RSM_DIR))
    return RedirectResponse("/?triggered=Gateway-Check+gestartet", status_code=303)


@app.get("/admin/gateway/screen")
async def gateway_screen():
    """Live-Screenshot des Gateway-Bildschirms (Login/2FA/Settings) — per
    Forced-Command-SSH-Key, der ausschliesslich scrot ausfuehren kann.
    """
    if not (config.GATEWAY_HOST and config.GATEWAY_SCREENSHOT_KEY):
        return HTMLResponse("Gateway-Screenshot nicht konfiguriert.", status_code=503)
    try:
        result = subprocess.run(
            ["ssh", "-i", config.GATEWAY_SCREENSHOT_KEY,
             "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
             f"root@{config.GATEWAY_HOST}"],
            capture_output=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return HTMLResponse("Screenshot-Timeout.", status_code=504)
    if result.returncode != 0 or not result.stdout:
        return HTMLResponse(
            f"Screenshot fehlgeschlagen: {result.stderr.decode(errors='replace')}", status_code=502)
    return Response(content=result.stdout, media_type="image/png")


@app.get("/admin/log")
async def admin_log(task: str = "w3"):
    log_map = {"w3": "data/cron.log", "eod": "data/eod.log"}
    log_file = RSM_DIR / log_map.get(task, "data/cron.log")
    if not log_file.exists():
        return HTMLResponse("Keine Log-Datei gefunden.", media_type="text/plain")
    lines = log_file.read_text(errors="replace").splitlines()
    return HTMLResponse(
        "<pre style='background:#0f1117;color:#e2e4ec;padding:16px;font-size:12px;'>"
        + "\n".join(lines[-100:])
        + "</pre>"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=config.PORT, reload=False)
