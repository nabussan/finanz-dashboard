import json
import math
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import db
from routers.micro_score import score_tickers, write_to_db_sync

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

# In-Memory-Status für laufende Universe-Berechnung (nicht persistent)
_rank_status: dict = {"status": "idle", "last_run": None}


async def _load_clusters(pool) -> list[dict]:
    rows = await pool.fetch("SELECT id, name FROM watchlists ORDER BY name")
    return [dict(r) for r in rows]


_SUB_SCORES_AVAILABLE: bool | None = None  # cached per process


async def _check_sub_scores(pool) -> bool:
    global _SUB_SCORES_AVAILABLE
    if _SUB_SCORES_AVAILABLE is not None:
        return _SUB_SCORES_AVAILABLE
    try:
        await pool.fetchval("SELECT score_trends FROM fundamentals LIMIT 1")
        _SUB_SCORES_AVAILABLE = True
    except Exception:
        _SUB_SCORES_AVAILABLE = False
    return _SUB_SCORES_AVAILABLE


async def _load_from_db(pool, cluster_id: int | None) -> list[dict]:
    sub = await _check_sub_scores(pool)
    sub_cols_f  = ", f.score_trends, f.score_cashflow, f.score_profitability, f.score_valuation, f.score_liquidity, f.score_solvency" if sub else ""
    sub_cols_bare = ", score_trends, score_cashflow, score_profitability, score_valuation, score_liquidity, score_solvency" if sub else ""

    if cluster_id is not None:
        rows = await pool.fetch(
            f"""
            SELECT f.ticker, f.updated, f.source, f.exchange,
                   f.pe, f.ev_ebitda, f.roe, f.debt_equity, f.roic, f.revenue_growth,
                   f.ranking_score, f.ranking_pos{sub_cols_f}
            FROM fundamentals f
            JOIN watchlist_items wi
              ON upper(split_part(wi.tv_symbol, ':', 2)) = upper(f.ticker)
              OR upper(wi.tv_symbol) = upper(f.ticker)
            WHERE wi.watchlist_id = $1
              AND f.updated = (SELECT MAX(updated) FROM fundamentals)
            ORDER BY f.ranking_score DESC NULLS LAST
            """,
            cluster_id,
        )
    else:
        rows = await pool.fetch(
            f"""
            SELECT ticker, updated, source, exchange,
                   pe, ev_ebitda, roe, debt_equity, roic, revenue_growth,
                   ranking_score, ranking_pos{sub_cols_bare}
            FROM fundamentals
            WHERE updated = (SELECT MAX(updated) FROM fundamentals)
            ORDER BY ranking_score DESC NULLS LAST
            """
        )
    return [dict(r) for r in rows]


def _load_cluster_json(cluster_id: int | None) -> list[dict] | None:
    if cluster_id is None:
        return None
    p = config.MICRO_CLUSTER_DIR / f"{cluster_id}.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return payload.get("tickers", [])
    except Exception:
        return None


def _cluster_json_ts(cluster_id: int | None) -> str | None:
    if cluster_id is None:
        return None
    p = config.MICRO_CLUSTER_DIR / f"{cluster_id}.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return payload.get("scored_at")
    except Exception:
        return None


@router.get("/micro", response_class=HTMLResponse)
async def micro_page(request: Request, cluster_id: int | None = None):
    pool = await db.get_pool()
    clusters = await _load_clusters(pool)

    # Cluster-JSON bevorzugen (cluster-relativ), dann DB-Fallback
    tickers = _load_cluster_json(cluster_id)
    source = "cluster-json"
    scored_at = _cluster_json_ts(cluster_id)

    if not tickers:
        tickers = await _load_from_db(pool, cluster_id)
        source = "db" if tickers else "json-fallback"
        scored_at = tickers[0]["updated"].isoformat() if tickers else None

    # JSON-Fallback wenn DB leer
    if not tickers:
        tickers = _load_from_json_fallback()
        source = "json-fallback"

    # Cluster-Rang (1..N) neu vergeben aus Cluster-JSON
    # Bei DB-Daten: ranking_pos aus DB verwenden
    if source == "cluster-json":
        for i, t in enumerate(tickers, 1):
            t.setdefault("cluster_rank", i)
            t["universe_rank"] = None
    else:
        for i, t in enumerate(tickers, 1):
            t["cluster_rank"] = i
            t["universe_rank"] = t.get("ranking_pos")

    active_cluster = next((c for c in clusters if c["id"] == cluster_id), None)

    return templates.TemplateResponse(
        request, "micro.html",
        {
            "tickers": tickers,
            "clusters": clusters,
            "active_cluster_id": cluster_id,
            "active_cluster": active_cluster,
            "source": source,
            "scored_at": scored_at,
            "json_dir_ok": config.MICRO_JSON_DIR.exists(),
            "rank_status": _rank_status["status"],
        },
    )


@router.post("/micro/rank")
async def micro_rank(
    request: Request,
    background_tasks: BackgroundTasks,
    cluster_id: int | None = None,
):
    pool = await db.get_pool()

    # Ticker-Liste für dieses Cluster holen
    if cluster_id is not None:
        rows = await pool.fetch(
            "SELECT tv_symbol FROM watchlist_items WHERE watchlist_id = $1",
            cluster_id,
        )
        tv_symbols = [r["tv_symbol"] for r in rows]
        cluster_name_rows = await pool.fetch(
            "SELECT name FROM watchlists WHERE id = $1", cluster_id
        )
        cluster_name = cluster_name_rows[0]["name"] if cluster_name_rows else str(cluster_id)
    else:
        tv_symbols = []
        cluster_name = "Universe"

    # Synchrones Cluster-Scoring (schnell: N Ticker)
    results = score_tickers(tv_symbols, config.MICRO_JSON_DIR, config.MICRO_CONFIG_PATH)

    # Cluster-JSON schreiben
    if cluster_id is not None:
        config.MICRO_CLUSTER_DIR.mkdir(parents=True, exist_ok=True)
        out = {
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "scored_at": datetime.now().isoformat(timespec="seconds"),
            "tickers": results,
        }
        (config.MICRO_CLUSTER_DIR / f"{cluster_id}.json").write_text(
            json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8"
        )

    # Universe-Scoring asynchron im Hintergrund (DB-Update)
    if config.DB_URL:
        _rank_status["status"] = "running"
        background_tasks.add_task(_universe_score_task)

    redirect_url = f"/micro?cluster_id={cluster_id}" if cluster_id else "/micro"
    return RedirectResponse(redirect_url, status_code=303)


def _universe_score_task():
    global _rank_status
    try:
        results = score_tickers([], config.MICRO_JSON_DIR, config.MICRO_CONFIG_PATH)
        n = write_to_db_sync(results, config.DB_URL)
        _rank_status = {"status": "done", "last_run": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:
        _rank_status = {"status": "idle", "last_run": None}


@router.get("/micro/rank/status")
async def micro_rank_status():
    from fastapi.responses import JSONResponse
    return JSONResponse(_rank_status)


def _load_from_json_fallback() -> list[dict]:
    if not config.MICRO_JSON_DIR.exists():
        return []
    results = []
    for f in sorted(config.MICRO_JSON_DIR.glob("financial_data_*.json"))[:50]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            desc = data.get("Descriptive Data", {})
            ticker = desc.get("Ticker") or f.stem.replace("financial_data_", "")
            results.append({
                "ticker": ticker,
                "exchange": desc.get("Exchange", ""),
                "source": "tv-json",
                "ranking_score": None,
                "score_trends": None,
                "score_cashflow": None,
                "score_profitability": None,
                "cluster_rank": None,
                "universe_rank": None,
                "pe": None, "roe": None, "debt_equity": None, "ev_ebitda": None,
            })
        except Exception:
            continue
    return results
