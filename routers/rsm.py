from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


_DEFAULT_DIR = {"ticker": "asc", "signal": "asc", "score": "desc", "z_score": "desc", "iv_rank": "desc"}


@router.get("/rsm", response_class=HTMLResponse)
async def rsm_page(request: Request, sort: str = "score", dir: str = ""):
    pool = await db.get_pool()
    allowed_sorts = {"score", "ticker", "signal", "z_score", "iv_rank"}
    order_col = sort if sort in allowed_sorts else "score"
    order_dir = dir if dir in ("asc", "desc") else _DEFAULT_DIR.get(order_col, "desc")
    nulls = "LAST" if order_dir == "desc" else "FIRST"

    rows = await pool.fetch(
        f"""
        SELECT ticker, run_date, signal, score, z_score, iv_rank, vrp, ann_return,
               klasse, klasse_updated, run_at AT TIME ZONE 'Europe/Berlin' AS run_at
        FROM signals
        WHERE run_date = (SELECT MAX(run_date) FROM signals)
        ORDER BY {order_col} {order_dir.upper()} NULLS {nulls}
        """
    )
    if rows and rows[0]["run_at"]:
        last_run = rows[0]["run_at"].strftime("%d.%m.%Y %H:%M")
    elif rows:
        last_run = rows[0]["run_date"].strftime("%d.%m.%Y")
    else:
        last_run = None
    klasse_dates = [r["klasse_updated"] for r in rows if r["klasse_updated"] is not None]
    klasse_stand = min(klasse_dates) if klasse_dates else None
    return templates.TemplateResponse(
        request, "rsm.html",
        {
            "signals": [dict(r) for r in rows],
            "last_run": last_run,
            "sort": order_col,
            "dir": order_dir,
            "klasse_stand": klasse_stand,
        },
    )
