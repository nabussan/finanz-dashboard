import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import db
from routers.micro_score import score_tickers, write_to_db_sync
from routers._cluster_shared import (
    classify_ibkr_coverage, upsert_cluster, assign_view, insert_items, trigger_ondemand_update,
)

_UNTRACKED_LIST_NAME = "Manuell ergänzt"

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _ampel(col: str, value) -> str:
    cfg = config.MICRO_AMPEL.get(col)
    if cfg is None or value is None or value != value:  # None oder NaN
        return ""
    if cfg["hi"]:
        return "ticker-stat--green" if value >= cfg["green"] \
          else "ticker-stat--orange" if value >= cfg["orange"] \
          else "ticker-stat--red"
    else:
        return "ticker-stat--green" if value <= cfg["green"] \
          else "ticker-stat--orange" if value <= cfg["orange"] \
          else "ticker-stat--red"


templates.env.globals["ampel"] = _ampel

# In-Memory-Status für laufende Universe-Berechnung (nicht persistent)
_rank_status: dict = {"status": "idle", "last_run": None}


async def _load_clusters(pool) -> list[dict]:
    rows = await pool.fetch(
        """SELECT c.id, c.name, COUNT(ci.tv_symbol) AS item_count
           FROM clusters c
           JOIN cluster_views cv ON cv.cluster_id = c.id
           LEFT JOIN cluster_items ci ON ci.cluster_id = c.id
           WHERE cv.view_name = 'micro'
           GROUP BY c.id, c.name ORDER BY c.name"""
    )
    clusters = [dict(r) for r in rows]
    for c in clusters:
        pf = config.MICRO_CLUSTER_DIR / f"{c['id']}.fetch_status.json"
        if pf.exists():
            try:
                c["fetch_status"] = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                c["fetch_status"] = None
        else:
            c["fetch_status"] = None
    return clusters


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

    # has_prices: gibt es fuer dieses Ticker/Exchange-Paar bereits eine IBKR-Kurshistorie
    # (rsm_prices)? Fundamentaldaten (TV-Scrape) und Kurshistorie (IBKR via rsm-live) sind
    # zwei getrennte Pipelines -- ein Ticker kann gescored sein, ohne je getrackt worden zu
    # sein (z.B. NRT: frueher echte Position, dann verkauft, nie in eine Watchlist/Cluster
    # aufgenommen). has_prices steuert den "Zu Liste hinzufuegen"-Hinweis im Chart-Panel.
    has_prices_expr = """
        EXISTS (
            SELECT 1 FROM rsm_prices rp
            WHERE rp.ticker = CASE WHEN {alias}exchange IS NOT NULL AND {alias}exchange <> ''
                                    THEN {alias}exchange || ':' || {alias}ticker
                                    ELSE {alias}ticker END
        ) AS has_prices
    """

    if cluster_id is not None:
        rows = await pool.fetch(
            f"""
            SELECT f.ticker, f.updated, f.source, f.exchange,
                   f.pe, f.ev_ebitda, f.roe, f.debt_equity, f.roic, f.revenue_growth,
                   f.ranking_score, f.ranking_pos{sub_cols_f},
                   {has_prices_expr.format(alias='f.')}
            FROM fundamentals f
            JOIN cluster_items ci
              ON upper(split_part(ci.tv_symbol, ':', 2)) = upper(f.ticker)
              OR upper(ci.tv_symbol) = upper(f.ticker)
              OR upper(split_part(ci.tv_symbol, ':', 2) || '_' || split_part(ci.tv_symbol, ':', 1)) = upper(f.ticker)
            WHERE ci.cluster_id = $1
              AND f.updated = (SELECT MAX(updated) FROM fundamentals)
            ORDER BY f.ranking_score DESC NULLS LAST
            """,
            cluster_id,
        )
    else:
        rows = await pool.fetch(
            f"""
            SELECT DISTINCT f.ticker, f.updated, f.source, f.exchange,
                   f.pe, f.ev_ebitda, f.roe, f.debt_equity, f.roic, f.revenue_growth,
                   f.ranking_score, f.ranking_pos{sub_cols_bare.replace(', ', ', f.')},
                   {has_prices_expr.format(alias='f.')}
            FROM fundamentals f
            WHERE f.updated = (SELECT MAX(updated) FROM fundamentals)
              AND EXISTS (
                SELECT 1 FROM cluster_items ci
                JOIN clusters c ON c.id = ci.cluster_id
                JOIN cluster_views cv ON cv.cluster_id = c.id
                WHERE cv.view_name = 'micro'
                  AND (
                    upper(split_part(ci.tv_symbol, ':', 2)) = upper(f.ticker)
                    OR upper(ci.tv_symbol) = upper(f.ticker)
                    OR upper(split_part(ci.tv_symbol, ':', 2) || '_' || split_part(ci.tv_symbol, ':', 1)) = upper(f.ticker)
                  )
              )
            ORDER BY f.ranking_score DESC NULLS LAST
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

    # Cluster-JSON bevorzugen (cluster-relativ), dann DB
    tickers = _load_cluster_json(cluster_id)
    source = "cluster-json"
    scored_at = _cluster_json_ts(cluster_id)

    if not tickers:
        tickers = await _load_from_db(pool, cluster_id)
        source = "db" if tickers else "empty"
        scored_at = tickers[0]["updated"].isoformat() if tickers else None

    # Universe-Scores + Rang + has_prices für Cluster-JSON-View aus DB nachladen
    if source == "cluster-json" and cluster_id and tickers:
        ticker_names = [t["ticker"].upper() for t in tickers if t.get("ticker")]
        if ticker_names:
            univ_rows = await pool.fetch(
                """SELECT upper(f.ticker) AS ticker,
                          f.ranking_score AS score_universe,
                          f.ranking_pos AS universe_rank,
                          f.exchange,
                          EXISTS (
                            SELECT 1 FROM rsm_prices rp
                            WHERE rp.ticker = CASE
                              WHEN f.exchange IS NOT NULL AND f.exchange <> ''
                              THEN f.exchange || ':' || f.ticker
                              ELSE f.ticker END
                          ) AS has_prices
                   FROM fundamentals f
                   WHERE f.updated = (SELECT MAX(updated) FROM fundamentals)
                   AND upper(f.ticker) = ANY($1::text[])""",
                ticker_names,
            )
            univ_map = {r["ticker"]: dict(r) for r in univ_rows}
            for t in tickers:
                row = univ_map.get(t["ticker"].upper(), {})
                t["score_universe"] = row.get("score_universe")
                t["universe_rank"] = row.get("universe_rank")
                t["has_prices"] = row.get("has_prices", False)

    # Cluster-Rang (1..N) neu vergeben aus Cluster-JSON
    # Bei DB-Daten: ranking_pos aus DB verwenden
    if source == "cluster-json":
        for i, t in enumerate(tickers, 1):
            t.setdefault("cluster_rank", i)
            t.setdefault("universe_rank", None)
    else:
        for i, t in enumerate(tickers, 1):
            t["cluster_rank"] = i
            t["universe_rank"] = t.get("ranking_pos")

    active_cluster = next((c for c in clusters if c["id"] == cluster_id), None)

    n_universe = await pool.fetchval(
        "SELECT COUNT(*) FROM fundamentals "
        "WHERE updated = (SELECT MAX(updated) FROM fundamentals) AND ranking_pos IS NOT NULL"
    ) or 0

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
            "micro_cluster_dir": str(config.MICRO_CLUSTER_DIR),
            "n_universe": n_universe,
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
            "SELECT tv_symbol FROM cluster_items WHERE cluster_id = $1",
            cluster_id,
        )
        tv_symbols = [r["tv_symbol"] for r in rows]
        cluster_name_rows = await pool.fetch(
            "SELECT name FROM clusters WHERE id = $1", cluster_id
        )
        cluster_name = cluster_name_rows[0]["name"] if cluster_name_rows else str(cluster_id)
    else:
        # Universe = alle Ticker aus allen Micro-Clustern (nicht alle JSON-Dateien)
        rows = await pool.fetch(
            """SELECT DISTINCT ci.tv_symbol FROM cluster_items ci
               JOIN clusters c ON c.id = ci.cluster_id
               JOIN cluster_views cv ON cv.cluster_id = c.id
               WHERE cv.view_name = 'micro'"""
        )
        tv_symbols = [r["tv_symbol"] for r in rows]
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

    # DB-Schreiben + Universe-Rang asynchron im Hintergrund (kein JSON-Lesen)
    if config.DB_URL:
        _rank_status["status"] = "running"
        background_tasks.add_task(_db_write_and_rank_task, results)

    redirect_url = f"/micro?cluster_id={cluster_id}" if cluster_id else "/micro"
    return RedirectResponse(redirect_url, status_code=303)


def _db_write_and_rank_task(results: list[dict]):
    """Scores in DB schreiben, dann ranking_pos aus bestehenden Scores neu berechnen (kein JSON-Lesen)."""
    import asyncio
    import asyncpg
    global _rank_status

    async def _rerank():
        con = await asyncpg.connect(config.DB_URL)
        try:
            await con.execute("""
                UPDATE fundamentals f
                SET ranking_pos = sub.pos
                FROM (
                    SELECT ticker, exchange, updated,
                           ROW_NUMBER() OVER (ORDER BY ranking_score DESC NULLS LAST) AS pos
                    FROM fundamentals
                    WHERE updated = (SELECT MAX(updated) FROM fundamentals)
                ) sub
                WHERE f.ticker = sub.ticker
                  AND f.exchange = sub.exchange
                  AND f.updated = sub.updated
            """)
        finally:
            await con.close()

    try:
        write_to_db_sync(results, config.DB_URL)
        asyncio.run(_rerank())
        _rank_status = {"status": "done", "last_run": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:
        _rank_status = {"status": "idle", "last_run": None}


@router.get("/micro/rank/status")
async def micro_rank_status():
    return JSONResponse(_rank_status)


@router.post("/micro/track")
async def micro_track(tv_symbol: str = Form(...)):
    """Nimmt einen Fundamentaldaten-only-Ticker (kein has_prices) in eine micro-Liste auf,
    damit rsm-live ihn ueber collect_db_tickers() automatisch fuer den naechsten
    OHLCV-Fetch mitnimmt. Landet in einer gemeinsamen 'Manuell ergänzt'-Liste statt
    eine konkrete Liste abzufragen -- haelt den Button im Chart-Panel klick-und-fertig."""
    pool = await db.get_pool()
    list_id = await upsert_cluster(pool, _UNTRACKED_LIST_NAME)
    await assign_view(pool, list_id, "micro")
    status = classify_ibkr_coverage(tv_symbol)
    await insert_items(pool, list_id, [tv_symbol], [status])
    if status == "resolved":
        trigger_ondemand_update()
    return JSONResponse({"tv_symbol": tv_symbol, "ibkr_status": status, "list_id": list_id})


_RSM_DB: Path | None = None


def _get_rsm_db() -> Path | None:
    global _RSM_DB
    if _RSM_DB is not None:
        return _RSM_DB if _RSM_DB.exists() else None
    candidate = config.RSM_DATA_DIR / "rsm_data.db"
    _RSM_DB = candidate
    return candidate if candidate.exists() else None


@router.get("/micro/chart-data/{symbol:path}")
async def micro_chart_data(symbol: str, interval: str = "1week"):
    """OHLCV-Daten aus rsm_data.db für symbol (EXCHANGE:TICKER).
    interval: '1week' oder '1day'. 404 wenn keine Daten vorhanden → TV-Widget-Fallback im Client."""
    db_path = _get_rsm_db()
    if db_path is None:
        raise HTTPException(404, "RSM-Datenbank nicht gefunden")

    if interval not in ("1week", "1day"):
        raise HTTPException(400, "interval muss '1week' oder '1day' sein")

    try:
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT date, open, high, low, close FROM prices "
            "WHERE ticker = ? AND interval = ? ORDER BY date",
            (symbol, interval),
        ).fetchall()
        con.close()
    except Exception as e:
        raise HTTPException(500, f"DB-Fehler: {e}")

    if not rows:
        raise HTTPException(404, f"Keine Preisdaten für {symbol} ({interval})")

    ohlc = [
        {"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
        for r in rows
        if all(v is not None for v in r)
    ]
    return JSONResponse({"symbol": symbol, "interval": interval, "ohlc": ohlc})


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
                "pe": None, "roe": None, "roic": None, "debt_equity": None,
                "ev_ebitda": None, "revenue_growth": None, "has_prices": False,
            })
        except Exception:
            continue
    return results
