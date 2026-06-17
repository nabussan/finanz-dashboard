from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from routers._cluster_shared import (
    parse_tv_import, upsert_cluster, insert_items,
    tickers_missing_prices, trigger_ondemand_update,
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
            r1.close AS current_price,
            r1.date  AS last_update,
            CASE
                WHEN r2.close IS NOT NULL AND r2.close <> 0
                THEN ROUND(((r1.close - r2.close) / r2.close) * 100, 2)
            END AS change_pct
        FROM cluster_items i
        LEFT JOIN LATERAL (
            SELECT close, date FROM rsm_prices
            WHERE ticker = i.tv_symbol AND interval = '1day'
            ORDER BY date DESC LIMIT 1
        ) r1 ON TRUE
        LEFT JOIN LATERAL (
            SELECT close FROM rsm_prices
            WHERE ticker = i.tv_symbol AND interval = '1day' AND date < r1.date
            ORDER BY date DESC LIMIT 1
        ) r2 ON TRUE
        WHERE i.cluster_id = $1
        ORDER BY i.tv_symbol
        """,
        list_id,
    )

    return templates.TemplateResponse(
        request, "portfolio_lists.html",
        {
            "all_lists": all_lists,
            "items": [dict(r) for r in items],
            "active_list": active_list,
            "imported": imported,
            "missing": missing,
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
