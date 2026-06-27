import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
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
    rows = await pool.fetch(
        """
        SELECT c.id, c.name,
               COUNT(ci.tv_symbol) AS item_count
        FROM clusters c
        LEFT JOIN cluster_items ci ON ci.cluster_id = c.id
        WHERE c.kind = $1
        GROUP BY c.id, c.name
        ORDER BY c.name
        """,
        _KIND,
    )
    all_lists = [dict(r) for r in rows]

    # Fetch-Status für jede Liste aus Progress-Files
    for lst in all_lists:
        pf = _progress_file(lst["id"])
        if pf.exists():
            try:
                lst["fetch_status"] = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                lst["fetch_status"] = None
        else:
            lst["fetch_status"] = None

    if not all_lists:
        return templates.TemplateResponse(
            request, "micro_lists.html",
            {"all_lists": [], "active_list": None},
        )
    return templates.TemplateResponse(
        request, "micro_lists.html",
        {"all_lists": all_lists, "active_list": None},
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
        "SELECT id, name FROM clusters WHERE kind = $1 ORDER BY name", _KIND
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

    # Pipeline-Status für dieses Cluster
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
            sys.executable, str(config.MICRO_SCRAPER_PATH),
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
    """Startet den TV-Playwright-Scraper für eine einzelne Micro-Liste."""
    _start_fetch(list_id)
    return RedirectResponse(f"/micro-listen/{list_id}", status_code=303)


@router.post("/micro-listen/fetch-multi")
async def fetch_multi(list_ids: str = Form(...)):
    """
    Startet sequentielle Fetches für mehrere Listen (Checkbox-Auswahl aus der Übersicht).
    list_ids: kommagetrennte IDs, z.B. "3,7,12"
    """
    ids = [int(x.strip()) for x in list_ids.split(",") if x.strip().isdigit()]
    for lid in ids:
        _start_fetch(lid)
    return RedirectResponse("/micro-listen", status_code=303)


@router.get("/micro-listen/{list_id}/pipeline-status")
async def pipeline_status(list_id: int):
    """Gibt aktuellen Fetch- und Rank-Status als JSON zurück (für HTMX-Polling)."""
    # Fetch-Status aus Progress-File
    pf = _progress_file(list_id)
    if pf.exists():
        try:
            fetch_data = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            fetch_data = {"status": "idle"}
    else:
        fetch_data = {"status": "idle"}

    # Rank-Status aus Cluster-JSON
    cluster_json = config.MICRO_CLUSTER_DIR / f"{list_id}.json"
    if cluster_json.exists():
        try:
            cj = json.loads(cluster_json.read_text(encoding="utf-8"))
            rank_data = {
                "status": "done",
                "scored_at": cj.get("scored_at"),
                "count": len(cj.get("tickers", [])),
            }
            # Als veraltet markieren wenn Fetch neuer als letzter Rank
            if fetch_data.get("finished_at") and cj.get("scored_at"):
                if fetch_data["finished_at"] > cj["scored_at"]:
                    rank_data["status"] = "stale"
        except Exception:
            rank_data = {"status": "idle"}
    else:
        rank_data = {"status": "idle"}

    return JSONResponse({"fetch": fetch_data, "rank": rank_data})
