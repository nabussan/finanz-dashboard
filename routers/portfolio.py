from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import db
import config
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


templates.env.globals.update(
    score_class=_score_class, ivr_class=_ivr_class,
    vrp_class=_vrp_class,     rend_class=_rend_class,
)

_VALID_BROKERS = {"ibkr", "ibkr-h", "sonstige"}


@router.get("/portfolio")
async def portfolio_redirect():
    return RedirectResponse("/portfolios", status_code=301)


@router.get("/portfolios", response_class=HTMLResponse)
async def portfolios_page(request: Request, broker: str = Query("ibkr")):
    if broker not in _VALID_BROKERS:
        broker = "ibkr"

    pool = await db.get_pool()

    # positions.ticker is bare (e.g. "FTNT"); signals/rsm_prices use TV format ("NASDAQ:FTNT").
    # Join via SPLIT_PART to extract the bare symbol from TV format for matching.
    positions = await pool.fetch(
        """
        SELECT
            p.ticker,
            p.entry_date, p.entry_price, p.qty, p.stop_price, p.broker,
            r.close      AS current_price,
            ROUND(((r.close - p.entry_price) / p.entry_price) * 100, 2) AS pnl_pct,
            p.updated AT TIME ZONE 'Europe/Berlin' AS updated,
            s.tv_symbol,
            s.score, s.iv_rank, s.vrp, s.ann_return, s.signal, s.klasse, s.klasse_updated
        FROM positions p
        LEFT JOIN LATERAL (
            SELECT ticker AS tv_symbol, score, iv_rank, vrp, ann_return, signal, klasse, klasse_updated
            FROM signals
            WHERE SPLIT_PART(ticker, ':', 2) = p.ticker
            ORDER BY run_date DESC LIMIT 1
        ) s ON TRUE
        LEFT JOIN LATERAL (
            SELECT close FROM rsm_prices
            WHERE SPLIT_PART(ticker, ':', 2) = p.ticker AND interval = '1day'
            ORDER BY date DESC LIMIT 1
        ) r ON TRUE
        WHERE
            CASE
                WHEN $1 = 'sonstige' THEN p.broker NOT IN ('ibkr', 'ibkr-h')
                ELSE p.broker = $1
            END
        ORDER BY s.score DESC NULLS LAST, pnl_pct DESC NULLS LAST
        """,
        broker,
    )

    rows = [dict(p) for p in positions]
    klasse_dates = [r["klasse_updated"] for r in rows if r["klasse_updated"] is not None]
    klasse_stand = min(klasse_dates) if klasse_dates else None

    charts_available = config.RSM_PORTFOLIO_HTML.exists()
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "positions": rows,
            "active_broker": broker,
            "charts_available": charts_available,
            "klasse_stand": klasse_stand,
        },
    )


@router.post("/portfolios/reclassify")
async def reclassify_portfolio(broker: str = Query("ibkr")):
    if broker not in _VALID_BROKERS:
        broker = "ibkr"
    pool = await db.get_pool()
    rows = await pool.fetch(
        """
        SELECT tv_symbol FROM positions
        WHERE tv_symbol IS NOT NULL AND tv_symbol LIKE '%:%'
          AND CASE WHEN $1 = 'sonstige' THEN broker NOT IN ('ibkr', 'ibkr-h') ELSE broker = $1 END
        """,
        broker,
    )
    trigger_reclassify([r["tv_symbol"] for r in rows])
    return RedirectResponse(f"/portfolios?broker={broker}&reclassify=1", status_code=303)


@router.get("/portfolio-charts")
async def portfolio_charts():
    if not config.RSM_PORTFOLIO_HTML.exists():
        return HTMLResponse("<p>Keine Charts verfügbar.</p>", status_code=404)
    return FileResponse(config.RSM_PORTFOLIO_HTML, media_type="text/html")


@router.get("/portfolio-charts-daily")
async def portfolio_charts_daily():
    if not config.RSM_PORTFOLIO_DAILY_HTML.exists():
        return HTMLResponse("<p>Keine Daily-Charts verfügbar.</p>", status_code=404)
    return FileResponse(config.RSM_PORTFOLIO_DAILY_HTML, media_type="text/html")
