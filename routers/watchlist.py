import re
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import db
from routers._cluster_shared import (
    parse_tv_import, upsert_cluster, insert_items,
    tickers_missing_prices, trigger_ondemand_update, trigger_reclassify,
    delete_item, delete_cluster,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_KIND = "watchlist"


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

def _z_class(v):
    if v is None: return ""
    return "ticker-stat--green" if v > 0.5 else "ticker-stat--red" if v < -0.5 else "ticker-stat--orange"

templates.env.globals.update(
    score_class=_score_class,
    ivr_class=_ivr_class,
    vrp_class=_vrp_class,
    rend_class=_rend_class,
    z_class=_z_class,
)


def _anchor(tv_symbol: str) -> str:
    """Same transform as make_charts.py: non-alphanumeric → '_'."""
    return "pnl-" + re.sub(r'[^a-zA-Z0-9]', '_', tv_symbol)


@router.get("/watchlists", response_class=HTMLResponse)
async def watchlists_default(request: Request):
    pool = await db.get_pool()
    first = await pool.fetchrow(
        "SELECT id FROM clusters WHERE kind = $1 ORDER BY id LIMIT 1", _KIND
    )
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
    symbols = parse_tv_import(content)

    pool = await db.get_pool()
    wl_id = await upsert_cluster(pool, wl_name, _KIND)
    await insert_items(pool, wl_id, symbols)

    missing = await tickers_missing_prices(pool, wl_id)
    if missing:
        trigger_ondemand_update()
    return RedirectResponse(f"/watchlists/{wl_id}?imported={len(symbols)}&missing={len(missing)}", status_code=303)


@router.get("/watchlists/{wl_id}", response_class=HTMLResponse)
async def watchlist_page(request: Request, wl_id: int,
                         imported: int = 0, missing: int = 0):
    pool = await db.get_pool()

    all_wl = [dict(r) for r in await pool.fetch(
        "SELECT id, name FROM clusters WHERE kind = $1 ORDER BY id", _KIND
    )]
    active_wl = next((w for w in all_wl if w["id"] == wl_id), None)
    if active_wl is None:
        return RedirectResponse("/watchlists", status_code=302)

    items = await pool.fetch(
        """
        SELECT
            ci.tv_symbol,
            ci.ticker,
            ci.added,
            s.score, s.z_score, s.iv_rank, s.vrp, s.ann_return, s.signal, s.klasse, s.klasse_updated
        FROM cluster_items ci
        LEFT JOIN LATERAL (
            SELECT score, z_score, iv_rank, vrp, ann_return, signal, klasse, klasse_updated
            FROM signals
            WHERE ticker = ci.tv_symbol
            ORDER BY run_date DESC LIMIT 1
        ) s ON TRUE
        WHERE ci.cluster_id = $1
        ORDER BY s.score DESC NULLS LAST, ci.tv_symbol
        """,
        wl_id,
    )

    rows = []
    klasse_dates = []
    for r in items:
        row = dict(r)
        row["chart_url"] = f"/portfolio-charts#{_anchor(row['tv_symbol'])}"
        rows.append(row)
        if row["klasse_updated"] is not None:
            klasse_dates.append(row["klasse_updated"])
    klasse_stand = min(klasse_dates) if klasse_dates else None

    return templates.TemplateResponse(
        request, "watchlist.html",
        {
            "all_watchlists": all_wl,
            "items": rows,
            "active_wl": active_wl,
            "imported": imported,
            "missing": missing,
            "charts_available": config.RSM_PORTFOLIO_HTML.exists(),
            "klasse_stand": klasse_stand,
            "ucits_map": config.UCITS_MAP,
        },
    )


@router.post("/watchlists")
async def create_watchlist(name: str = Form(...)):
    pool = await db.get_pool()
    wl_id = await upsert_cluster(pool, name.strip(), _KIND)
    return RedirectResponse(f"/watchlists/{wl_id}", status_code=303)


@router.post("/watchlists/{wl_id}/import")
async def import_watchlist(wl_id: int, content: str = Form(...)):
    symbols = parse_tv_import(content)
    pool = await db.get_pool()
    await insert_items(pool, wl_id, symbols)
    missing = await tickers_missing_prices(pool, wl_id)
    if missing:
        trigger_ondemand_update()
    return RedirectResponse(
        f"/watchlists/{wl_id}?imported={len(symbols)}&missing={len(missing)}", status_code=303
    )


@router.post("/watchlists/{wl_id}/delete")
async def delete_watchlist(wl_id: int):
    pool = await db.get_pool()
    await delete_cluster(pool, wl_id)
    return RedirectResponse("/watchlists", status_code=303)


@router.post("/watchlists/{wl_id}/delete-item")
async def delete_watchlist_item(wl_id: int, tv_symbol: str = Form(...)):
    pool = await db.get_pool()
    await delete_item(pool, wl_id, tv_symbol)
    return RedirectResponse(f"/watchlists/{wl_id}", status_code=303)


@router.post("/watchlists/{wl_id}/reclassify")
async def reclassify_watchlist(wl_id: int):
    pool = await db.get_pool()
    rows = await pool.fetch("SELECT tv_symbol FROM cluster_items WHERE cluster_id = $1", wl_id)
    trigger_reclassify([r["tv_symbol"] for r in rows])
    return RedirectResponse(f"/watchlists/{wl_id}?reclassify=1", status_code=303)
