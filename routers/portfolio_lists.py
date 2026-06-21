from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from routers._cluster_shared import (
    parse_tv_import, upsert_cluster, insert_items,
    tickers_missing_prices, trigger_ondemand_update, trigger_reclassify,
    delete_item, delete_cluster,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_KIND = "portfolio_list"


@router.get("/portfolio-listen", response_class=HTMLResponse)
async def portfolio_listen_default(request: Request):
    pool = await db.get_pool()
    first = await pool.fetchrow(
        "SELECT id FROM clusters WHERE kind = $1 ORDER BY id LIMIT 1", _KIND
    )
    if first:
        return RedirectResponse(f"/portfolio-listen/{first['id']}", status_code=302)
    return templates.TemplateResponse(
        request, "portfolio_lists.html",
        {"all_lists": [], "items": [], "active_list": None, "missing": 0},
    )


# NOTE: /portfolio-listen/upload must be defined BEFORE /portfolio-listen/{list_id}
@router.post("/portfolio-listen/upload")
async def upload_portfolio_list(file: UploadFile = File(...)):
    list_name = Path(file.filename).stem
    content = (await file.read()).decode("utf-8", errors="ignore")
    symbols = parse_tv_import(content)
    pool = await db.get_pool()
    list_id = await upsert_cluster(pool, list_name, _KIND)
    await insert_items(pool, list_id, symbols)

    missing = await tickers_missing_prices(pool, list_id)
    if missing:
        trigger_ondemand_update()
    return RedirectResponse(
        f"/portfolio-listen/{list_id}?imported={len(symbols)}&missing={len(missing)}", status_code=303
    )


@router.get("/portfolio-listen/{list_id}", response_class=HTMLResponse)
async def portfolio_listen_page(request: Request, list_id: int,
                                imported: int = 0, missing: int = 0):
    pool = await db.get_pool()

    all_lists = [dict(r) for r in await pool.fetch(
        "SELECT id, name FROM clusters WHERE kind = $1 ORDER BY id", _KIND
    )]
    active_list = next((l for l in all_lists if l["id"] == list_id), None)
    if active_list is None:
        return RedirectResponse("/portfolio-listen", status_code=302)

    items = await pool.fetch(
        """
        SELECT
            i.tv_symbol,
            COALESCE(lq.price, eod_latest.close) AS current_price,
            COALESCE(lq.updated_at::date, eod_latest.date) AS last_update,
            CASE
                WHEN lq.price IS NOT NULL AND eod_latest.close IS NOT NULL AND eod_latest.close <> 0
                    THEN ROUND(((lq.price - eod_latest.close) / eod_latest.close) * 100, 2)
                WHEN lq.price IS NULL AND r2.close IS NOT NULL AND r2.close <> 0
                    THEN ROUND(((eod_latest.close - r2.close) / r2.close) * 100, 2)
            END AS change_pct,
            s.klasse_updated
        FROM cluster_items i
        LEFT JOIN LATERAL (
            SELECT price, updated_at FROM live_quotes
            WHERE ticker = i.tv_symbol
        ) lq ON TRUE
        LEFT JOIN LATERAL (
            SELECT close, date FROM rsm_prices
            WHERE ticker = i.tv_symbol AND interval = '1day'
            ORDER BY date DESC LIMIT 1
        ) eod_latest ON TRUE
        LEFT JOIN LATERAL (
            SELECT close FROM rsm_prices
            WHERE ticker = i.tv_symbol AND interval = '1day' AND date < eod_latest.date
            ORDER BY date DESC LIMIT 1
        ) r2 ON TRUE
        LEFT JOIN LATERAL (
            SELECT klasse_updated FROM signals
            WHERE ticker = i.tv_symbol
            ORDER BY run_date DESC LIMIT 1
        ) s ON TRUE
        WHERE i.cluster_id = $1
        ORDER BY i.tv_symbol
        """,
        list_id,
    )
    rows = [dict(r) for r in items]
    klasse_dates = [r["klasse_updated"] for r in rows if r["klasse_updated"] is not None]
    klasse_stand = min(klasse_dates) if klasse_dates else None

    return templates.TemplateResponse(
        request, "portfolio_lists.html",
        {
            "all_lists": all_lists,
            "items": rows,
            "active_list": active_list,
            "imported": imported,
            "missing": missing,
            "klasse_stand": klasse_stand,
        },
    )


@router.post("/portfolio-listen")
async def create_portfolio_list(name: str = Form(...)):
    pool = await db.get_pool()
    list_id = await upsert_cluster(pool, name.strip(), _KIND)
    return RedirectResponse(f"/portfolio-listen/{list_id}", status_code=303)


@router.post("/portfolio-listen/{list_id}/import")
async def import_portfolio_list(list_id: int, content: str = Form(...)):
    symbols = parse_tv_import(content)
    pool = await db.get_pool()
    await insert_items(pool, list_id, symbols)
    missing = await tickers_missing_prices(pool, list_id)
    if missing:
        trigger_ondemand_update()
    return RedirectResponse(
        f"/portfolio-listen/{list_id}?imported={len(symbols)}&missing={len(missing)}", status_code=303
    )


@router.post("/portfolio-listen/{list_id}/delete")
async def delete_portfolio_list(list_id: int):
    pool = await db.get_pool()
    await delete_cluster(pool, list_id)
    return RedirectResponse("/portfolio-listen", status_code=303)


@router.post("/portfolio-listen/{list_id}/delete-item")
async def delete_portfolio_list_item(list_id: int, tv_symbol: str = Form(...)):
    pool = await db.get_pool()
    await delete_item(pool, list_id, tv_symbol)
    return RedirectResponse(f"/portfolio-listen/{list_id}", status_code=303)


@router.post("/portfolio-listen/{list_id}/reclassify")
async def reclassify_portfolio_list(list_id: int):
    pool = await db.get_pool()
    rows = await pool.fetch("SELECT tv_symbol FROM cluster_items WHERE cluster_id = $1", list_id)
    trigger_reclassify([r["tv_symbol"] for r in rows])
    return RedirectResponse(f"/portfolio-listen/{list_id}?reclassify=1", status_code=303)
