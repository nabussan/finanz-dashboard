"""Finanz Dashboard — FastAPI entry point."""
import asyncio
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    pool = await db.get_pool()
    systems = await _system_status(pool)
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
