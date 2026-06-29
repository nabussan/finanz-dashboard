from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import db
from routers._cluster_shared import trigger_reclassify

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


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



@router.get("/watchlists", response_class=HTMLResponse)
async def watchlists_default(request: Request):
    pool = await db.get_pool()
    first = await pool.fetchrow(
        """SELECT c.id FROM clusters c
           JOIN cluster_views cv ON cv.cluster_id = c.id
           WHERE cv.view_name = 'watchlist' ORDER BY c.id LIMIT 1"""
    )
    if first:
        return RedirectResponse(f"/watchlists/{first['id']}", status_code=302)
    return templates.TemplateResponse(
        request, "watchlist.html",
        {"all_watchlists": [], "items": [], "active_wl": None, "missing": 0},
    )


@router.get("/watchlists/{wl_id}", response_class=HTMLResponse)
async def watchlist_page(request: Request, wl_id: int, reclassify: int = 0):
    pool = await db.get_pool()

    all_wl = [dict(r) for r in await pool.fetch(
        """SELECT c.id, c.name FROM clusters c
           JOIN cluster_views cv ON cv.cluster_id = c.id
           WHERE cv.view_name = 'watchlist' ORDER BY c.id"""
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
            "klasse_stand": klasse_stand,
            "ucits_map": config.UCITS_MAP,
            "reclassify": reclassify,
        },
    )


@router.post("/watchlists/{wl_id}/reclassify")
async def reclassify_watchlist(wl_id: int):
    pool = await db.get_pool()
    rows = await pool.fetch("SELECT tv_symbol FROM cluster_items WHERE cluster_id = $1", wl_id)
    trigger_reclassify([r["tv_symbol"] for r in rows])
    return RedirectResponse(f"/watchlists/{wl_id}?reclassify=1", status_code=303)
