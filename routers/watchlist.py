import re
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_SYMBOL_RE = re.compile(r'^[A-Z0-9]+:[A-Z0-9.]+$')


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


def _anchor(tv_symbol: str) -> str:
    """Same transform as make_charts.py: non-alphanumeric → '_'."""
    return "pnl-" + re.sub(r'[^a-zA-Z0-9]', '_', tv_symbol)


def _parse_tv_import(content: str) -> list[str]:
    """Parse comma- or newline-separated TV symbols (EXCHANGE:TICKER)."""
    symbols = []
    for part in re.split(r'[,\n\r]+', content):
        part = part.strip()
        if not part or part.startswith('#'):
            continue
        if ':' in part and _SYMBOL_RE.match(part):
            symbols.append(part)
    return list(dict.fromkeys(symbols))  # deduplicate, preserve order


async def _count_missing_prices(pool, wl_id: int) -> int:
    """Count watchlist tickers that have no signals data yet."""
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM watchlist_items wi
        WHERE wi.watchlist_id = $1
        AND NOT EXISTS (
            SELECT 1 FROM signals WHERE ticker = wi.tv_symbol LIMIT 1
        )
        """,
        wl_id,
    )


async def _upsert_watchlist(pool, name: str) -> int:
    """Create watchlist if not exists, return id."""
    row = await pool.fetchrow(
        "INSERT INTO watchlists (name) VALUES ($1) ON CONFLICT (name) DO NOTHING RETURNING id",
        name,
    )
    return row["id"] if row else await pool.fetchval(
        "SELECT id FROM watchlists WHERE name = $1", name
    )


@router.get("/watchlists", response_class=HTMLResponse)
async def watchlists_default(request: Request):
    pool = await db.get_pool()
    first = await pool.fetchrow("SELECT id FROM watchlists ORDER BY id LIMIT 1")
    if first:
        return RedirectResponse(f"/watchlists/{first['id']}", status_code=302)
    return templates.TemplateResponse(
        request, "watchlist.html",
        {"all_watchlists": [], "items": [], "active_wl": None, "missing": 0,
         "charts_available": config.RSM_PORTFOLIO_HTML.exists()},
    )


# NOTE: /watchlists/upload must be defined BEFORE /watchlists/{wl_id}
@router.post("/watchlists/upload")
async def upload_watchlist(file: UploadFile = File(...)):
    """Upload a .txt file — filename becomes watchlist name, content is comma/newline-separated symbols."""
    wl_name = Path(file.filename).stem
    content = (await file.read()).decode("utf-8", errors="ignore")
    symbols = _parse_tv_import(content)

    pool = await db.get_pool()
    wl_id = await _upsert_watchlist(pool, wl_name)

    if symbols:
        await pool.executemany(
            "INSERT INTO watchlist_items (watchlist_id, tv_symbol) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            [(wl_id, sym) for sym in symbols],
        )

    missing = await _count_missing_prices(pool, wl_id)
    return RedirectResponse(f"/watchlists/{wl_id}?imported={len(symbols)}&missing={missing}", status_code=303)


@router.get("/watchlists/{wl_id}", response_class=HTMLResponse)
async def watchlist_page(request: Request, wl_id: int,
                         imported: int = 0, missing: int = 0):
    pool = await db.get_pool()

    all_wl = [dict(r) for r in await pool.fetch(
        "SELECT id, name FROM watchlists ORDER BY id"
    )]
    active_wl = next((w for w in all_wl if w["id"] == wl_id), None)
    if active_wl is None:
        return RedirectResponse("/watchlists", status_code=302)

    items = await pool.fetch(
        """
        SELECT
            wi.tv_symbol,
            wi.ticker,
            wi.added,
            s.score, s.iv_rank, s.vrp, s.ann_return, s.signal, s.klasse
        FROM watchlist_items wi
        LEFT JOIN LATERAL (
            SELECT score, iv_rank, vrp, ann_return, signal, klasse
            FROM signals
            WHERE ticker = wi.tv_symbol
            ORDER BY run_date DESC LIMIT 1
        ) s ON TRUE
        WHERE wi.watchlist_id = $1
        ORDER BY s.score DESC NULLS LAST, wi.tv_symbol
        """,
        wl_id,
    )

    rows = []
    for r in items:
        row = dict(r)
        row["chart_url"] = f"/portfolio-charts#{_anchor(row['tv_symbol'])}"
        rows.append(row)

    return templates.TemplateResponse(
        request, "watchlist.html",
        {
            "all_watchlists": all_wl,
            "items": rows,
            "active_wl": active_wl,
            "imported": imported,
            "missing": missing,
            "charts_available": config.RSM_PORTFOLIO_HTML.exists(),
        },
    )


@router.post("/watchlists")
async def create_watchlist(name: str = Form(...)):
    pool = await db.get_pool()
    wl_id = await _upsert_watchlist(pool, name.strip())
    return RedirectResponse(f"/watchlists/{wl_id}", status_code=303)


@router.post("/watchlists/{wl_id}/import")
async def import_watchlist(wl_id: int, content: str = Form(...)):
    symbols = _parse_tv_import(content)
    pool = await db.get_pool()
    if symbols:
        await pool.executemany(
            "INSERT INTO watchlist_items (watchlist_id, tv_symbol) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            [(wl_id, sym) for sym in symbols],
        )
    missing = await _count_missing_prices(pool, wl_id)
    return RedirectResponse(
        f"/watchlists/{wl_id}?imported={len(symbols)}&missing={missing}", status_code=303
    )


@router.post("/watchlists/{wl_id}/delete")
async def delete_watchlist(wl_id: int):
    pool = await db.get_pool()
    await pool.execute("DELETE FROM watchlists WHERE id = $1", wl_id)
    return RedirectResponse("/watchlists", status_code=303)


@router.post("/watchlists/{wl_id}/delete-item")
async def delete_watchlist_item(wl_id: int, tv_symbol: str = Form(...)):
    pool = await db.get_pool()
    await pool.execute(
        "DELETE FROM watchlist_items WHERE watchlist_id = $1 AND tv_symbol = $2",
        wl_id, tv_symbol,
    )
    return RedirectResponse(f"/watchlists/{wl_id}", status_code=303)
