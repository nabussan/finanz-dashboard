from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from routers._cluster_shared import (
    parse_tv_import, parse_micro_import, classify_ibkr_coverage,
    upsert_cluster, insert_items, assign_view, unassign_view,
    delete_item, delete_cluster,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_ALL_VIEWS = ("watchlist", "micro", "portfolio")


@router.get("/clusters", response_class=HTMLResponse)
async def clusters_page(request: Request):
    pool = await db.get_pool()
    rows = await pool.fetch(
        """SELECT c.id, c.name, c.created,
                  COUNT(DISTINCT ci.tv_symbol) AS item_count,
                  ARRAY_AGG(DISTINCT cv.view_name ORDER BY cv.view_name)
                    FILTER (WHERE cv.view_name IS NOT NULL) AS views
           FROM clusters c
           LEFT JOIN cluster_items ci ON ci.cluster_id = c.id
           LEFT JOIN cluster_views cv ON cv.cluster_id = c.id
           GROUP BY c.id, c.name, c.created ORDER BY c.name"""
    )
    clusters = [dict(r) for r in rows]
    for c in clusters:
        c["views"] = list(c["views"] or [])
    return templates.TemplateResponse(
        request, "clusters.html",
        {"clusters": clusters, "all_views": _ALL_VIEWS},
    )


@router.post("/clusters")
async def create_cluster(name: str = Form(...)):
    pool = await db.get_pool()
    cid = await upsert_cluster(pool, name.strip())
    return RedirectResponse(f"/clusters#{cid}", status_code=303)


@router.post("/clusters/upload")
async def upload_cluster(file: UploadFile = File(...), mode: str = Form("tv")):
    name = Path(file.filename).stem
    content = (await file.read()).decode("utf-8", errors="ignore")
    pool = await db.get_pool()
    cid = await upsert_cluster(pool, name)
    await _do_import(pool, cid, content, mode)
    return RedirectResponse(f"/clusters#{cid}", status_code=303)


@router.post("/clusters/{cid}/rename")
async def rename_cluster(cid: int, name: str = Form(...)):
    pool = await db.get_pool()
    await pool.execute("UPDATE clusters SET name = $1 WHERE id = $2", name.strip(), cid)
    return RedirectResponse(f"/clusters#{cid}", status_code=303)


@router.post("/clusters/{cid}/delete")
async def delete_cluster_route(cid: int):
    pool = await db.get_pool()
    await delete_cluster(pool, cid)
    return RedirectResponse("/clusters", status_code=303)


@router.post("/clusters/{cid}/import")
async def import_cluster(cid: int, content: str = Form(...), mode: str = Form("tv"),
                         next: str = Form(default=None)):
    pool = await db.get_pool()
    await _do_import(pool, cid, content, mode)
    return RedirectResponse(next or f"/clusters#{cid}", status_code=303)


@router.post("/clusters/{cid}/delete-item")
async def delete_cluster_item(cid: int, tv_symbol: str = Form(...),
                               next: str = Form(default=None)):
    pool = await db.get_pool()
    await delete_item(pool, cid, tv_symbol)
    return RedirectResponse(next or f"/clusters#{cid}", status_code=303)


@router.post("/clusters/{cid}/assign")
async def assign_cluster_view(cid: int, view_name: str = Form(...)):
    pool = await db.get_pool()
    await assign_view(pool, cid, view_name)
    return RedirectResponse(f"/clusters#{cid}", status_code=303)


@router.post("/clusters/{cid}/unassign")
async def unassign_cluster_view(cid: int, view_name: str = Form(...)):
    pool = await db.get_pool()
    await unassign_view(pool, cid, view_name)
    return RedirectResponse(f"/clusters#{cid}", status_code=303)


async def _do_import(pool, cluster_id: int, content: str, mode: str) -> None:
    if mode == "micro":
        symbols = parse_micro_import(content)
        statuses = [classify_ibkr_coverage(s) for s in symbols]
        await insert_items(pool, cluster_id, symbols, statuses)
    else:
        symbols = parse_tv_import(content)
        await insert_items(pool, cluster_id, symbols)
