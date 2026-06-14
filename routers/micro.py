from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import config
import db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


async def _load_from_db(pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT ticker, updated, source, pe, ev_ebitda, roe,
               debt_equity, revenue_growth, ranking_score, ranking_pos
        FROM fundamentals
        WHERE updated = (SELECT MAX(updated) FROM fundamentals)
        ORDER BY ranking_pos ASC NULLS LAST
        """
    )
    return [dict(r) for r in rows]


def _load_from_json() -> list[dict]:
    """Fallback: liest per-Ticker JSONs direkt (TV-Format)."""
    import json

    if not config.MICRO_JSON_DIR.exists():
        return []

    results = []
    for f in sorted(config.MICRO_JSON_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            data.setdefault("ticker", f.stem)
            data["source"] = "tv (json)"
            results.append(data)
        except Exception:
            continue
    return results


@router.get("/micro", response_class=HTMLResponse)
async def micro_page(request: Request):
    pool = await db.get_pool()
    tickers = await _load_from_db(pool)

    db_empty = len(tickers) == 0
    if db_empty:
        tickers = _load_from_json()

    return templates.TemplateResponse(
        request, "micro.html",
        {
            "tickers": tickers,
            "source": "json-fallback" if db_empty else "db",
            "json_dir_ok": config.MICRO_JSON_DIR.exists(),
        },
    )
