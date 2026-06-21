from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from routers._cluster_shared import (
    parse_micro_import, classify_ibkr_coverage, upsert_cluster, insert_items,
    tickers_missing_prices, trigger_ondemand_update,
    delete_item, delete_cluster,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_KIND = "micro_list"


async def _import_symbols(pool, list_id: int, content: str) -> tuple[int, int]:
    """Parst + klassifiziert + inserted. Gibt (resolved_count, unresolved_count) zurück."""
    symbols = parse_micro_import(content)
    statuses = [classify_ibkr_coverage(s) for s in symbols]
    await insert_items(pool, list_id, symbols, statuses)

    resolved = [s for s, st in zip(symbols, statuses) if st == "resolved"]
    if resolved:
        missing = await tickers_missing_prices(pool, list_id)
        # nur fuer tatsaechlich IBKR-aufloesbare Ticker den Sofort-Fetch ausloesen
        if any(m in resolved for m in missing):
            trigger_ondemand_update()
    return len(resolved), len(statuses) - len(resolved)


@router.get("/micro-listen", response_class=HTMLResponse)
async def micro_listen_default(request: Request):
    pool = await db.get_pool()
    first = await pool.fetchrow(
        "SELECT id FROM clusters WHERE kind = $1 ORDER BY id LIMIT 1", _KIND
    )
    if first:
        return RedirectResponse(f"/micro-listen/{first['id']}", status_code=302)
    return templates.TemplateResponse(
        request, "micro_lists.html",
        {"all_lists": [], "items": [], "active_list": None},
    )


# NOTE: /micro-listen/upload muss VOR /micro-listen/{list_id} stehen
@router.post("/micro-listen/upload")
async def upload_micro_list(file: UploadFile = File(...)):
    list_name = Path(file.filename).stem
    content = (await file.read()).decode("utf-8", errors="ignore")
    pool = await db.get_pool()
    list_id = await upsert_cluster(pool, list_name, _KIND)
    resolved, unresolved = await _import_symbols(pool, list_id, content)
    return RedirectResponse(
        f"/micro-listen/{list_id}?resolved={resolved}&unresolved={unresolved}", status_code=303
    )


@router.get("/micro-listen/{list_id}", response_class=HTMLResponse)
async def micro_listen_page(request: Request, list_id: int,
                             resolved: int = 0, unresolved: int = 0):
    pool = await db.get_pool()

    all_lists = [dict(r) for r in await pool.fetch(
        "SELECT id, name FROM clusters WHERE kind = $1 ORDER BY id", _KIND
    )]
    active_list = next((l for l in all_lists if l["id"] == list_id), None)
    if active_list is None:
        return RedirectResponse("/micro-listen", status_code=302)

    items = [dict(r) for r in await pool.fetch(
        "SELECT tv_symbol, ibkr_status FROM cluster_items WHERE cluster_id = $1 ORDER BY tv_symbol",
        list_id,
    )]
    resolved_items = [i for i in items if i["ibkr_status"] == "resolved"]
    unresolved_items = [i for i in items if i["ibkr_status"] != "resolved"]

    return templates.TemplateResponse(
        request, "micro_lists.html",
        {
            "all_lists": all_lists,
            "active_list": active_list,
            "resolved_items": resolved_items,
            "unresolved_items": unresolved_items,
            "imported_resolved": resolved,
            "imported_unresolved": unresolved,
        },
    )


@router.post("/micro-listen")
async def create_micro_list(name: str = Form(...)):
    pool = await db.get_pool()
    list_id = await upsert_cluster(pool, name.strip(), _KIND)
    return RedirectResponse(f"/micro-listen/{list_id}", status_code=303)


@router.post("/micro-listen/{list_id}/import")
async def import_micro_list(list_id: int, content: str = Form(...)):
    pool = await db.get_pool()
    resolved, unresolved = await _import_symbols(pool, list_id, content)
    return RedirectResponse(
        f"/micro-listen/{list_id}?resolved={resolved}&unresolved={unresolved}", status_code=303
    )


@router.post("/micro-listen/{list_id}/delete")
async def delete_micro_list(list_id: int):
    pool = await db.get_pool()
    await delete_cluster(pool, list_id)
    return RedirectResponse("/micro-listen", status_code=303)


@router.post("/micro-listen/{list_id}/delete-item")
async def delete_micro_list_item(list_id: int, tv_symbol: str = Form(...)):
    pool = await db.get_pool()
    await delete_item(pool, list_id, tv_symbol)
    return RedirectResponse(f"/micro-listen/{list_id}", status_code=303)
