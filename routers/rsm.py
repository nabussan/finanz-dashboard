from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/rsm", response_class=HTMLResponse)
async def rsm_page(request: Request, sort: str = "score"):
    pool = await db.get_pool()
    allowed_sorts = {"score", "ticker", "signal", "z_score", "iv_rank"}
    order_col = sort if sort in allowed_sorts else "score"

    rows = await pool.fetch(
        f"""
        SELECT ticker, run_date, signal, score, z_score, iv_rank, vrp, ann_return, klasse
        FROM signals
        WHERE run_date = (SELECT MAX(run_date) FROM signals)
        ORDER BY {order_col} DESC NULLS LAST
        """
    )
    last_run = rows[0]["run_date"] if rows else None
    return templates.TemplateResponse(
        request, "rsm.html",
        {"signals": [dict(r) for r in rows], "last_run": last_run, "sort": order_col},
    )
