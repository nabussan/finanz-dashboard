import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/micro-listen", response_class=HTMLResponse)
async def micro_listen_default(request: Request):
    pool = await db.get_pool()
    rows = await pool.fetch(
        """
        SELECT c.id, c.name,
               COUNT(ci.tv_symbol) AS item_count
        FROM clusters c
        JOIN cluster_views cv ON cv.cluster_id = c.id
        LEFT JOIN cluster_items ci ON ci.cluster_id = c.id
        WHERE cv.view_name = 'micro'
        GROUP BY c.id, c.name
        ORDER BY c.name
        """,
    )
    all_lists = [dict(r) for r in rows]

    for lst in all_lists:
        pf = _progress_file(lst["id"])
        if pf.exists():
            try:
                lst["fetch_status"] = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                lst["fetch_status"] = None
        else:
            lst["fetch_status"] = None
        lst["has_ranking"] = (config.MICRO_CLUSTER_DIR / f"{lst['id']}.json").exists()

    return templates.TemplateResponse(
        request, "micro_lists.html",
        {"all_lists": all_lists, "active_list": None},
    )


@router.get("/micro-listen/{list_id}", response_class=HTMLResponse)
async def micro_listen_page(request: Request, list_id: int,
                             resolved: int = 0, unresolved: int = 0):
    pool = await db.get_pool()

    all_lists = [dict(r) for r in await pool.fetch(
        """SELECT c.id, c.name FROM clusters c
           JOIN cluster_views cv ON cv.cluster_id = c.id
           WHERE cv.view_name = 'micro' ORDER BY c.name"""
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

    pf = _progress_file(list_id)
    fetch_status = None
    if pf.exists():
        try:
            fetch_status = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            pass

    cluster_json = config.MICRO_CLUSTER_DIR / f"{list_id}.json"
    rank_status = None
    if cluster_json.exists():
        try:
            cj = json.loads(cluster_json.read_text(encoding="utf-8"))
            rank_status = {"scored_at": cj.get("scored_at"), "count": len(cj.get("tickers", []))}
            if fetch_status and fetch_status.get("finished_at") and cj.get("scored_at"):
                if fetch_status["finished_at"] > cj["scored_at"]:
                    rank_status["stale"] = True
        except Exception:
            pass

    return templates.TemplateResponse(
        request, "micro_lists.html",
        {
            "all_lists": all_lists,
            "active_list": active_list,
            "resolved_items": resolved_items,
            "unresolved_items": unresolved_items,
            "imported_resolved": resolved,
            "imported_unresolved": unresolved,
            "fetch_status": fetch_status,
            "rank_status": rank_status,
        },
    )


# ── Pipeline-Endpoints ────────────────────────────────────────────────────────

def _progress_file(list_id: int) -> Path:
    config.MICRO_CLUSTER_DIR.mkdir(parents=True, exist_ok=True)
    return config.MICRO_CLUSTER_DIR / f"{list_id}.fetch_status.json"


def _start_fetch(list_id: int) -> None:
    """Startet fetch_fundamentals.py als entkoppelten Subprocess."""
    pf = _progress_file(list_id)
    pf.write_text(json.dumps({
        "cluster_id": list_id, "status": "running",
        "total": 0, "to_scrape": 0, "done": 0, "skipped": 0,
        "errors": 0, "error_log": [],
        "started_at": datetime.now().isoformat(), "finished_at": None,
    }, ensure_ascii=False), encoding="utf-8")
    subprocess.Popen(
        [
            str(config.MICRO_SCRAPER_PYTHON), str(config.MICRO_SCRAPER_PATH),
            "--cluster-id", str(list_id),
            "--db-url", config.DB_URL,
            "--json-dir", str(config.MICRO_JSON_DIR),
            "--config", str(config.MICRO_CONFIG_PATH),
            "--progress-file", str(pf),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@router.post("/micro-listen/{list_id}/fetch")
async def fetch_micro_list(list_id: int):
    _start_fetch(list_id)
    return RedirectResponse(f"/micro-listen/{list_id}", status_code=303)


@router.post("/micro-listen/fetch-multi")
async def fetch_multi(list_ids: str = Form(...)):
    ids = [int(x.strip()) for x in list_ids.split(",") if x.strip().isdigit()]
    for lid in ids:
        _start_fetch(lid)
    return RedirectResponse("/micro-listen", status_code=303)


@router.get("/micro-listen/{list_id}/pipeline-status")
async def pipeline_status(list_id: int):
    pf = _progress_file(list_id)
    if pf.exists():
        try:
            fetch_data = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            fetch_data = {"status": "idle"}
    else:
        fetch_data = {"status": "idle"}

    cluster_json = config.MICRO_CLUSTER_DIR / f"{list_id}.json"
    if cluster_json.exists():
        try:
            cj = json.loads(cluster_json.read_text(encoding="utf-8"))
            rank_data = {
                "status": "done",
                "scored_at": cj.get("scored_at"),
                "count": len(cj.get("tickers", [])),
            }
            if fetch_data.get("finished_at") and cj.get("scored_at"):
                if fetch_data["finished_at"] > cj["scored_at"]:
                    rank_data["status"] = "stale"
        except Exception:
            rank_data = {"status": "idle"}
    else:
        rank_data = {"status": "idle"}

    from fastapi.responses import JSONResponse
    return JSONResponse({"fetch": fetch_data, "rank": rank_data})
