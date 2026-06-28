"""Finanz Dashboard — FastAPI entry point."""
import asyncio
import json
import os
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import config
from routers import clusters, disco, rsm, portfolio, micro, micro_lists, watchlist, portfolio_lists

RSM_DIR = Path(__file__).parent.parent / "rsm-live"

try:
    from croniter import croniter as _croniter
    _CRONITER_AVAILABLE = True
except ImportError:
    _CRONITER_AVAILABLE = False

_PIPELINE_TASKS = {
    "intraday": ("Intraday-Kurse (Snapshot)", ["src/intraday_prices.py"]),
    "eod":    ("EOD-Update (OHLCV)",          ["src/eod_update.py", "--skip-ibkr"]),  # --skip-ibkr = kein Options-Screen
    "iv":     ("IV-Daten (IBKR Options)",      ["src/run_w3.py", "--ibkr-only"]),
    "scores": ("W3-Scores",                    ["src/run_w3.py", "--skip-update", "--skip-notify"]),
    "charts": ("Charts neu generieren",        ["src/make_charts.py"]),
    "classify": ("Klasse neu berechnen (alle Ticker)", ["src/determine_class.py"]),
    "full":   ("Alles (EOD + IV + Scores + Charts)", None),  # handled separately
}


def _is_today(dt) -> bool:
    if dt is None:
        return False
    d = dt.date() if hasattr(dt, "date") else dt
    return d == date.today()


def _task_running(task: str) -> bool:
    """'full' nutzt ondemand_update.sh's eigenes flock-Lock (erkennt JEDEN
    laufenden On-Demand-Lauf, nicht nur ueber den Button gestartete -- z.B.
    auch den Trigger aus trigger_ondemand_update() bei Listen-Imports).
    Die anderen Tasks haben kein eigenes Lock, daher PID-Datei aus admin_run().
    """
    if task == "full":
        lockfile = RSM_DIR / "data" / ".ondemand.lock"
        try:
            result = subprocess.run(
                ["flock", "-n", "-x", str(lockfile), "-c", "true"],
                capture_output=True, timeout=5,
            )
            return result.returncode != 0
        except Exception:
            return False
    pidfile = RSM_DIR / "data" / f".run_{task}.pid"
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


async def _pipeline_status(pool) -> dict[str, str]:
    """Ampel je Pipeline-Button: 'yellow' wenn aktuell aktiv, sonst 'green'
    wenn die jeweilige Datenquelle heute aktualisiert wurde, sonst 'red'.

    'iv' und 'scores' teilen sich denselben Frische-Indikator (signals.run_at),
    weil run_w3.py den Options-Screen IMMER mitlaufen laesst -- unabhaengig
    von --ibkr-only/--skip-update (siehe run_w3.py::run(), Schritt 3) --
    beide Buttons aktualisieren also tatsaechlich dasselbe Feld, es gibt
    keine getrennte Datenquelle, die sie unterscheiden wuerde.
    """
    intraday_max, eod_max, run_at_max = await asyncio.gather(
        pool.fetchval("SELECT max(updated_at) FROM live_quotes"),
        pool.fetchval("SELECT max(date) FROM rsm_prices WHERE interval = '1day'"),
        pool.fetchval("SELECT max(run_at) FROM signals"),
    )

    charts_file = RSM_DIR / "data" / "charts" / "portfolio.html"
    charts_fresh = charts_file.exists() and _is_today(
        datetime.fromtimestamp(charts_file.stat().st_mtime)
    )

    classify_fresh = False
    sqlite_path = RSM_DIR / "data" / "rsm_data.db"
    if sqlite_path.exists():
        try:
            conn = sqlite3.connect(str(sqlite_path))
            row = conn.execute("SELECT max(klasse_updated) FROM tickers").fetchone()
            conn.close()
            if row and row[0]:
                classify_fresh = _is_today(datetime.fromisoformat(row[0]))
        except Exception:
            pass

    full_marker = RSM_DIR / "data" / ".full_last_success"
    full_fresh = full_marker.exists() and _is_today(
        datetime.fromtimestamp(full_marker.stat().st_mtime)
    )

    fresh = {
        "intraday": _is_today(intraday_max),
        "eod": _is_today(eod_max),
        "iv": _is_today(run_at_max),
        "scores": _is_today(run_at_max),
        "charts": charts_fresh,
        "classify": classify_fresh,
        "full": full_fresh,
    }

    return {
        task: "yellow" if _task_running(task) else ("green" if ok else "red")
        for task, ok in fresh.items()
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

app.include_router(clusters.router)
app.include_router(disco.router)
app.include_router(rsm.router)
app.include_router(portfolio.router)
app.include_router(micro.router)
app.include_router(micro_lists.router)
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
    """IBKR-Gateway-Karte: liest gateway_state.json (Cron check_gateway.py).

    'wide': True auf allen Rückgaben — Karte ist breiter als die anderen
    (card--wide), damit die Reconnect/Re-Login-Hinweistexte in den Buttons
    nicht umbrechen und sich die Kartenbreite nicht je nach Status ändert.
    """
    from datetime import datetime

    name = "IBKR Gateway"
    check_now = {"url": "/admin/gateway/check-now", "label": "Jetzt prüfen"}
    links = [{"url": "/admin/gateway/screen", "label": "Gateway-Screen anzeigen"}] \
        if config.GATEWAY_SCREENSHOT_KEY else []

    if not config.GATEWAY_HOST:
        return {"name": name, "status": "grey", "label": "nicht konfiguriert", "url": None,
                "actions": [], "links": [], "wide": True}

    state = {}
    state_file = config.RSM_DATA_DIR / "gateway_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = {}

    age_min = None
    checked_dt = None
    if state.get("checked_at"):
        try:
            checked_dt = datetime.fromisoformat(state["checked_at"])
            age_min = (datetime.now() - checked_dt).total_seconds() / 60
        except Exception:
            age_min = None
    checked_str = checked_dt.strftime("%d.%m.%Y %H:%M:%S") if checked_dt else None

    if age_min is None or age_min > 20:
        label = "Health-Check nie gelaufen" if age_min is None else f"Health-Check seit {int(age_min)}min inaktiv (zuletzt geprüft: {checked_str})"
        return {"name": name, "status": "grey", "label": label, "url": None,
                "actions": [check_now], "links": links, "wide": True}

    if state.get("status") == "up":
        actions = [check_now]
        if config.GATEWAY_STOP_KEY:
            actions.append({
                "url": "/admin/gateway/stop", "label": "Gateway stoppen",
                "hint": "Gibt die IBKR-Session frei (z.B. fuer persoenliche TWS-"
                        "Anmeldung). Pausiert Kurs-Fetch + Stop-Loss-Alerts bis"
                        " 'Re-Login starten' wieder gedrueckt wird.",
            })
        return {"name": name, "status": "green", "label": f"verbunden (zuletzt geprüft: {checked_str})",
                "url": None, "actions": actions, "links": links, "wide": True}

    actions = []
    if config.GATEWAY_RECONNECT_KEY:
        actions.append({
            "url": "/admin/gateway/reconnect", "label": "Reconnect (Session-Konflikt)",
            "hint": "Erst probieren — kostenlos, sofort, kein 2FA.",
        })
    actions.append({
        "url": "/admin/gateway/restart", "label": "Re-Login starten",
        "hint": "Nur falls Reconnect 'kein Konflikt' meldet, Gateway aber"
                " down bleibt (Crash/Hang/2FA-Timeout). Voller Neustart, neues 2FA.",
    })
    actions.append(check_now)

    return {
        "name": name, "status": "red", "label": "nicht verbunden — Re-Login nötig", "url": None,
        "actions": actions,
        "links": links,
        "wide": True,
    }


@app.get("/api/live-prices")
async def api_live_prices():
    """Aktuelle Intraday-Preise aus live_quotes fuer die Chart-Preislinie."""
    pool = await db.get_pool()
    rows = await pool.fetch("SELECT ticker, price FROM live_quotes")
    return JSONResponse({r["ticker"]: float(r["price"]) for r in rows})


def _cron_schedule(pipeline_status: dict[str, str]) -> list[dict]:
    """Cron-Jobs mit berechnetem next_run (croniter) und last_run-Farbe."""
    now = datetime.now()
    rows = []
    for job in config.CRON_JOBS:
        if _CRONITER_AVAILABLE:
            try:
                nxt = _croniter(job["expr"], now).get_next(datetime)
                delta = nxt - now
                h, rem = divmod(int(delta.total_seconds()), 3600)
                m = rem // 60
                next_label = f"in {h}h {m:02d}m" if h else f"in {m}m"
            except Exception:
                next_label = "?"
        else:
            next_label = "—"

        task = job.get("task")
        if task:
            dot = pipeline_status.get(task, "grey")
        elif job["label"] == "Gateway-Health":
            gw = _gateway_status()
            dot = "green" if gw.get("status") == "up" else "red"
        else:
            dot = ""
        rows.append({**job, "next_label": next_label, "dot": dot})
    return rows


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    pool = await db.get_pool()
    systems = await _system_status(pool)
    systems.append(_gateway_status())
    pipeline_status = await _pipeline_status(pool)
    cron_jobs = _cron_schedule(pipeline_status)
    return templates.TemplateResponse(
        request, "index.html", {"systems": systems, "pipeline_status": pipeline_status, "cron_jobs": cron_jobs}
    )


@app.post("/admin/iv/refresh")
async def iv_refresh(ticker: str = Form(...)):
    """Schneller Einzel-Ticker-IV-Refresh (Sekunden statt der ~20-40 Min des
    vollen 'IV-Daten'-Laufs, der erst nach der gesamten Universe einen
    frischen run_at schreibt -- siehe update_iv_ticker.py-Docstring). Async
    Subprocess statt subprocess.run(), damit der Event-Loop waehrend der
    IBKR-Roundtrips nicht fuer alle Nutzer blockiert.
    """
    venv_python = str(RSM_DIR / ".venv" / "bin" / "python3")
    try:
        proc = await asyncio.create_subprocess_exec(
            venv_python, "src/update_iv_ticker.py", ticker,
            cwd=str(RSM_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        lines = [l for l in stdout.decode(errors="replace").splitlines() if l.strip()]
        data = json.loads(lines[-1]) if lines else {"ok": False, "error": "Keine Ausgabe"}
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "Timeout (60s)"}, status_code=504)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse(data)


@app.post("/admin/run/{task}")
async def admin_run(task: str):
    if task not in _PIPELINE_TASKS:
        return HTMLResponse("Unbekannte Task", status_code=400)

    venv_python = str(RSM_DIR / ".venv" / "bin" / "python3")

    # start_new_session=True loest den Subprozess vom Dashboard-Prozess
    # (gleiche Cgroup), sonst killt ein systemctl restart finanz-dashboard
    # (z.B. bei einem Code-Deploy waehrend ein Admin-Lauf aktiv ist) den
    # Pipeline-Lauf mit -- ohne sauberen Exit, ohne Telegram-Alert, ohne
    # Spur ausser einem abrupt abreissenden Log (Befund 2026-06-22: ein
    # Dashboard-Redeploy hat so einen seit 19:20 laufenden vollen W3-Lauf
    # stillschweigend getoetet). Gleiches Muster wie trigger_ondemand_update()
    # in routers/_cluster_shared.py.
    if task == "full":
        # ondemand_update.sh = eod_update.py (OHLCV+IV) && run_w3.py (Scores)
        # && make_charts.py -- exakt die Sequenz, die das Label verspricht.
        # Vorher zeigte "full" faelschlich auf run_w3_cron.sh, das den
        # EOD/OHLCV/IV-Schritt komplett ausliess (Befund 2026-06-23). Als
        # Nebeneffekt bringt ondemand_update.sh sein eigenes flock-Lock +
        # isolierte IBKR-Client-IDs (16/17) mit, schuetzt also auch gegen
        # Kollision mit einem parallel laufenden Cron-Lauf.
        script = str(RSM_DIR / "infra" / "ondemand_update.sh")
        subprocess.Popen(["bash", script], cwd=str(RSM_DIR), start_new_session=True)
        # 'full' braucht keine PID-Datei -- _task_running() prueft stattdessen
        # ondemand_update.sh's eigenes flock-Lock direkt (siehe _pipeline_status()).
    elif task == "eod":
        # eod-Update immer mit anschliessender Chart-Regenerierung verketten:
        # Neue Watchlist-Ticker erscheinen in portfolio.html nur nach make_charts.py.
        # bash-Subprozess haelt die PID-Datei bis beide Schritte abgeschlossen sind.
        cmd = f"{venv_python} src/eod_update.py --skip-ibkr && {venv_python} src/make_charts.py"
        proc = subprocess.Popen(["bash", "-c", cmd], cwd=str(RSM_DIR), start_new_session=True)
        (RSM_DIR / "data" / f".run_{task}.pid").write_text(str(proc.pid))
    else:
        _, args = _PIPELINE_TASKS[task]
        proc = subprocess.Popen([venv_python] + args, cwd=str(RSM_DIR), start_new_session=True)
        (RSM_DIR / "data" / f".run_{task}.pid").write_text(str(proc.pid))

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


@app.post("/admin/gateway/reconnect")
async def gateway_reconnect():
    """Klickt 'Reconnect This Session' im IBKR-Session-Konflikt-Dialog per
    SSH (Forced-Command-Key, xdotool) — fuer den Fall, dass eine parallele
    TWS-Anmeldung das Gateway aus der API-Session geworfen hat. Leichter
    als ein voller Re-Login (kein IBC-Neustart, kein erneutes 2FA).
    """
    if not (config.GATEWAY_HOST and config.GATEWAY_RECONNECT_KEY):
        return HTMLResponse("Gateway-Reconnect nicht konfiguriert.", status_code=503)
    try:
        result = subprocess.run(
            ["ssh", "-i", config.GATEWAY_RECONNECT_KEY,
             "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
             f"root@{config.GATEWAY_HOST}"],
            capture_output=True, timeout=15, text=True,
        )
    except subprocess.TimeoutExpired:
        return HTMLResponse("Reconnect-Timeout.", status_code=504)
    msg = "Reconnect+ausgeloest" if result.stdout.strip() == "clicked" else "Kein+Session-Konflikt+erkannt"
    return RedirectResponse(f"/?triggered={msg}", status_code=303)


@app.post("/admin/gateway/stop")
async def gateway_stop():
    """Stoppt den IBKR-Gateway-Prozess sauber (systemctl stop, kein Restart=
    always-Trigger -- ein expliziter stop ist kein Crash). Gegenstueck zu
    /admin/gateway/restart: gibt die IBKR-Session frei statt sie zurueckzu-
    holen, z.B. wenn eine persoenliche TWS-Anmeldung (gleicher IBKR-User) sie
    braucht. Bis zum naechsten 'Re-Login starten' pausieren Kurs-Fetch und
    Stop-Loss-Alerts -- bewusst in Kauf genommen, kurzzeitig.
    """
    if config.GATEWAY_HOST and config.GATEWAY_STOP_KEY:
        subprocess.Popen([
            "ssh", "-i", config.GATEWAY_STOP_KEY,
            "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
            f"root@{config.GATEWAY_HOST}", "stop",
        ])
    return RedirectResponse("/?triggered=Gateway+gestoppt", status_code=303)


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
