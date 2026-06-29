"""chart.py — On-demand single-ticker chart endpoint.

GET /chart/{tv_symbol}?period=W|D

Reads OHLCV from rsm_prices (PostgreSQL), computes W3/WillVal/Kalman
via rsm-live lib/, returns single-ticker HTML.

Replaces pre-generated portfolio.html / portfolio_daily.html from make_charts.py.
"""
import asyncio
import json
import math
import sys
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import db

# rsm-live library path: /opt/rsm-live on LXC 521, sibling repo in dev
_RSM_LIVE = "/opt/rsm-live"
if not Path(_RSM_LIVE).exists():
    _RSM_LIVE = str(Path(__file__).parent.parent.parent / "rsm-live")
if _RSM_LIVE not in sys.path:
    sys.path.insert(0, _RSM_LIVE)

from lib.signals import compute_w3_signal, _compute_willval, _linreg_slope  # noqa: E402
from lib.kalman import compute_kalman  # noqa: E402

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

W3_EXIT_BE = 13
W3_Z_ENTRY = 1.0
W3_N = 52
WV_SHORT = 10
WV_LONG = 30
WV_NORM = 52
SL_LEN = 5
BARS_W = 300
BARS_D = 350


def _dates(idx) -> list[str]:
    return [str(d)[:10] for d in idx]


def _clean(arr) -> list:
    out = []
    for v in arr:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            out.append(None)
        else:
            out.append(round(float(v), 4))
    return out


def _w2_state(wv, sl) -> list:
    result = []
    for wv_v, sl_v in zip(wv, sl):
        if pd.isna(wv_v) or pd.isna(sl_v):
            result.append(None)
        elif (wv_v > 60 and sl_v > 0) or (sl_v > 0 and 15 <= wv_v <= 40):
            result.append(1)
        elif wv_v > 60 or (sl_v > 0 and 40 < wv_v <= 60):
            result.append(0)
        else:
            result.append(-1)
    return result


async def _load_prices(pool, ticker: str, interval: str, bars: int) -> pd.DataFrame:
    rows = await pool.fetch(
        "SELECT date, open, high, low, close FROM rsm_prices "
        "WHERE ticker = $1 AND interval = $2 ORDER BY date DESC LIMIT $3",
        ticker, interval, bars,
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index().astype(float)
    return df


async def _get_benchmark(pool, ticker: str) -> str:
    row = await pool.fetchrow(
        "SELECT benchmark FROM cluster_items "
        "WHERE tv_symbol = $1 AND benchmark IS NOT NULL LIMIT 1",
        ticker,
    )
    return row["benchmark"] if row else "AMEX:SPY"


def _build_weekly(df_w: pd.DataFrame, bench_w: pd.DataFrame) -> dict | None:
    if df_w.empty or bench_w.empty:
        return None
    try:
        w3 = compute_w3_signal(df_w, bench_w, exit_be=W3_EXIT_BE, z_entry=W3_Z_ENTRY, n=W3_N)
    except Exception:
        return None

    ratio_w = df_w["close"] / bench_w["close"].reindex(df_w.index).ffill()
    wv_w = _compute_willval(ratio_w, short=WV_SHORT, long=WV_LONG, n=WV_NORM)
    sl_w = _linreg_slope(wv_w, length=SL_LEN)

    cuts = [s.first_valid_index() for s in [w3["z_slope"], wv_w] if s.first_valid_index() is not None]
    cutoff = max(cuts) if cuts else df_w.index[0]
    mask = df_w.index >= cutoff
    df_cut = df_w[mask]
    z_cut = w3["z_slope"][mask]
    wv_cut = wv_w[mask]
    sl_cut = sl_w[mask]

    ohlc = [
        {"time": str(i)[:10], "open": round(float(r.open), 4), "high": round(float(r.high), 4),
         "low": round(float(r.low), 4), "close": round(float(r.close), 4)}
        for i, r in df_cut.iterrows() if not any(pd.isna([r.open, r.high, r.low, r.close]))
    ]
    return {
        "ohlc": ohlc,
        "dates": _dates(df_cut.index),
        "z_slope": _clean(z_cut.values),
        "wv": _clean(wv_cut.values),
        "w2": _w2_state(wv_cut, sl_cut),
        "entries": [str(i)[:10] for i, v in w3["sig_entry"].items() if v],
        "exits": [str(i)[:10] for i, v in w3["sig_exit"].items() if v],
    }


def _build_daily(df_d: pd.DataFrame, bench_d: pd.DataFrame) -> dict | None:
    if df_d.empty or bench_d.empty:
        return None

    ratio_d = df_d["close"] / bench_d["close"].reindex(df_d.index).ffill()
    wv_d = _compute_willval(ratio_d, short=WV_SHORT, long=WV_LONG, n=WV_NORM)
    sl_d = _linreg_slope(wv_d, length=SL_LEN)

    try:
        kdf = compute_kalman(df_d)
    except Exception:
        kdf = None

    cutoff = wv_d.first_valid_index() or df_d.index[0]
    mask = df_d.index >= cutoff
    df_cut = df_d[mask]
    wv_cut = wv_d[mask]
    sl_cut = sl_d[mask]

    ohlc = [
        {"time": str(i)[:10], "open": round(float(r.open), 4), "high": round(float(r.high), 4),
         "low": round(float(r.low), 4), "close": round(float(r.close), 4)}
        for i, r in df_cut.iterrows() if not any(pd.isna([r.open, r.high, r.low, r.close]))
    ]
    result = {
        "ohlc": ohlc,
        "dates": _dates(df_cut.index),
        "wv": _clean(wv_cut.values),
        "w2": _w2_state(wv_cut, sl_cut),
    }

    if kdf is not None:
        kdf_cut = kdf[mask]
        kalman_arr = kdf_cut["kalman"].values
        atr_arr = kdf_cut["atr"].values
        result.update({
            "kalman": _clean(kalman_arr),
            "k_upper": _clean(kdf_cut["upper"].values),
            "k_lower": _clean(kdf_cut["lower"].values),
            "k_upper2": _clean(kalman_arr + 2.0 * atr_arr),
            "k_lower2": _clean(kalman_arr - 2.0 * atr_arr),
            "k_above": [bool(v) for v in kdf_cut["is_above"].values],
        })
        ia = kdf_cut["is_above"].values
        dates = _dates(df_cut.index)
        k_enter, k_exit = [], []
        for i in range(1, len(ia)):
            if not ia[i - 1] and ia[i]:
                k_enter.append(dates[i])
            elif ia[i - 1] and not ia[i]:
                k_exit.append(dates[i])
        result["k_enter"] = k_enter
        result["k_exit"] = k_exit

    return result


@router.get("/chart/{tv_symbol}", response_class=HTMLResponse)
async def chart_ondemand(request: Request, tv_symbol: str, period: str = Query("W")):
    period = period.upper()
    if period not in ("W", "D"):
        period = "W"

    pool = await db.get_pool()
    benchmark = await _get_benchmark(pool, tv_symbol)

    interval = "1week" if period == "W" else "1day"
    bars = BARS_W if period == "W" else BARS_D

    df, bench = await asyncio.gather(
        _load_prices(pool, tv_symbol, interval, bars),
        _load_prices(pool, benchmark, interval, bars),
    )

    builder = _build_weekly if period == "W" else _build_daily
    data = await asyncio.to_thread(builder, df, bench)

    if data is None:
        return HTMLResponse(
            "<body style='background:#131722;color:#787b86;padding:20px;"
            f"font-family:sans-serif'>Keine OHLCV-Daten für {tv_symbol}</body>"
        )

    return templates.TemplateResponse(
        request, "chart.html",
        {"ticker": tv_symbol, "period": period, "data_json": json.dumps(data, ensure_ascii=False)},
    )
