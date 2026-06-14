"""
Micro-Scoring-Adapter: Extrahiert Fundamental-Kennzahlen aus TV-JSON-Dateien
und berechnet Scores analog zu 130_json_verarbeiten.py (pandas-frei, kein Excel).

Kernfunktion: score_tickers(tv_symbols, json_dir, config_path)
  tv_symbols=[]   → Universe-Modus (alle JSON-Dateien)
  tv_symbols=[…]  → Cluster-Modus (nur diese N Ticker, cluster-relativ normalisiert)
"""

import json
import math
import logging
from datetime import date
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# ── Richtungslogik: True = höherer Wert ist besser ───────────────────────────
IS_BETTER_HIGHER: dict[str, bool] = {
    "Slope Total common shares outstanding": False,
    "Return on assets %": True,
    "Return on equity %": True,
    "Return on invested capital %": True,
    "Gross margin %": True,
    "Operating margin %": True,
    "EBITDA margin %": True,
    "Net margin %": True,
    "Debt-Weighted Profitability": True,
    "Quick ratio": True,
    "Current ratio": True,
    "Inventory turnover": True,
    "Asset turnover": True,
    "Price to earnings ratio": False,
    "Price to sales ratio": False,
    "Price to cash flow ratio": False,
    "Price to book ratio": False,
    "Enterprise value to EBITDA ratio": False,
    "Debt to assets ratio": False,
    "Debt to equity ratio": False,
    "Long term debt to total assets ratio": False,
    "Long term debt to total equity ratio": False,
    "TTM FCF CAGR": True,
    "TTM FCF-Marge": True,
    "Slope FCF": True,
    "Book Value Trend": True,
    "CapEx / Revenue (TTM)": False,
    "Net Debt / TTM Op. CF": False,
    "Slope Gross margin": True,
    "Slope Debt to equity": False,
    "Slope Debt-Weighted Profitability": True,
}

_US_EXCHANGES = {"NYSE", "NASDAQ", "OTC", "CBOE", "AMEX"}

# Profitability-Spalten für Negativfilter (wie in 130_json_verarbeiten.py)
_PROFIT_COLS = [
    "Return on assets %", "Return on equity %", "Return on invested capital %",
    "Gross margin %", "Operating margin %", "EBITDA margin %", "Net margin %",
]


# ── Hilfsfunktionen (1:1 aus 130_json_verarbeiten.py übernommen) ─────────────

def _nan() -> float:
    return float("nan")


def _is_nan(v) -> bool:
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return v is None


def _convert_to_float(value) -> float:
    if value is None:
        return _nan()
    if isinstance(value, str):
        try:
            value = value.replace("‪", "").replace("‬", "").replace(" ", "").strip()
            value = value.replace("−", "-").replace("—", "0").replace(",", ".")
            for suffix, mult in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
                if value.upper().endswith(suffix):
                    return float(value[:-1]) * mult
            return float(value)
        except (ValueError, TypeError):
            return _nan()
    elif isinstance(value, (int, float)):
        return float(value)
    return _nan()


def _get_latest_value(data_dict: dict) -> float:
    if not data_dict:
        return _nan()
    try:
        latest_date = sorted(data_dict.keys(), reverse=True)[0]
        return _convert_to_float(data_dict[latest_date])
    except Exception:
        return _nan()


def _get_ttm_sum(data_dict: dict) -> float:
    if not data_dict:
        return _nan()
    dates = sorted(data_dict.keys(), reverse=True)[:4]
    values = [_convert_to_float(data_dict.get(d)) for d in dates]
    valid = [v for v in values if not _is_nan(v)]
    return sum(valid) if valid else _nan()


def _get_last_n_values(data_dict: dict, n: int = 8) -> list[float]:
    if not data_dict:
        return []
    dates = sorted(data_dict.keys(), reverse=True)[:n]
    return [_convert_to_float(data_dict.get(d)) for d in dates]


def _calculate_slope(values: list) -> float:
    valid = [v for v in values if not _is_nan(v)]
    if len(valid) < 3:
        return _nan()
    num_points = min(len(valid), 8)
    recent = valid[-num_points:]
    oldest_first = recent[::-1]
    x = np.arange(num_points, dtype=float)
    y = np.array(oldest_first, dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    return float(slope)


def _calculate_cagr(begin: float, end: float, years: float = 2) -> float:
    if _is_nan(begin) or _is_nan(end) or begin <= 0 or end <= 0:
        return _nan()
    try:
        ratio = end / begin
        if ratio < 0:
            return _nan()
        return ratio ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError):
        return _nan()


def _json_path(ticker: str, exchange: str, json_dir: Path) -> Path:
    """Identische Logik wie micro/src/utils/paths.py::json_pfad()."""
    exc = (exchange or "").strip().upper()
    if exc and exc not in _US_EXCHANGES:
        return json_dir / f"financial_data_{ticker}_{exc}.json"
    return json_dir / f"financial_data_{ticker}.json"


# ── Kennzahlen aus einem JSON-File extrahieren ────────────────────────────────

def _extract(data: dict, ticker: str) -> dict[str, float]:
    """Gibt Dict mit allen Roh-Kennzahlen für einen Ticker zurück."""
    m: dict[str, float] = {"ticker": ticker}

    val = data.get("Valuation ratios", {})
    prof = data.get("Profitability ratios", {})
    liq = data.get("Liquidity ratios", {})
    sol = data.get("Solvency ratios", {})
    per = data.get("Per share metrics", {})
    desc = data.get("Descriptive Data", {})

    # Einfache Werte (latest)
    simple = {
        "Price to earnings ratio":             (val,  "Price to earnings ratio"),
        "Price to sales ratio":                (val,  "Price to sales ratio"),
        "Price to cash flow ratio":            (val,  "Price to cash flow ratio"),
        "Price to book ratio":                 (val,  "Price to book ratio"),
        "Enterprise value to EBITDA ratio":    (val,  "Enterprise value to EBITDA ratio"),
        "Return on assets %":                  (prof, "Return on assets %"),
        "Return on equity %":                  (prof, "Return on equity %"),
        "Return on invested capital %":        (prof, "Return on invested capital %"),
        "Gross margin %":                      (prof, "Gross margin %"),
        "Operating margin %":                  (prof, "Operating margin %"),
        "EBITDA margin %":                     (prof, "EBITDA margin %"),
        "Net margin %":                        (prof, "Net margin %"),
        "Quick ratio":                         (liq,  "Quick ratio"),
        "Current ratio":                       (liq,  "Current ratio"),
        "Inventory turnover":                  (liq,  "Inventory turnover"),
        "Asset turnover":                      (liq,  "Asset turnover"),
        "Debt to assets ratio":                (sol,  "Debt to assets ratio"),
        "Debt to equity ratio":                (sol,  "Debt to equity ratio"),
        "Long term debt to total assets ratio":(sol,  "Long term debt to total assets ratio"),
        "Long term debt to total equity ratio":(sol,  "Long term debt to total equity ratio"),
    }
    for col, (section, key) in simple.items():
        m[col] = _get_latest_value(section.get(key, {}))

    # Total common shares outstanding + slope
    shares_dict = desc.get("Total common shares outstanding", {})
    shares_vals = _get_last_n_values(shares_dict)
    valid_shares = [v for v in shares_vals if not _is_nan(v)]
    m["Total common shares outstanding"] = _get_latest_value(shares_dict)
    m["Slope Total common shares outstanding"] = (
        _calculate_slope(valid_shares) if len(valid_shares) >= 3 else _nan()
    )

    # Slopes: Gross margin, Debt to equity
    gm_vals = list(prof.get("Gross margin %", {}).values())
    valid_gm = [_convert_to_float(v) for v in gm_vals]
    m["Slope Gross margin"] = _calculate_slope(valid_gm) if len([v for v in valid_gm if not _is_nan(v)]) >= 3 else _nan()

    d2e_vals = list(sol.get("Debt to equity ratio", {}).values())
    valid_d2e = [_convert_to_float(v) for v in d2e_vals]
    m["Slope Debt to equity"] = _calculate_slope(valid_d2e) if len([v for v in valid_d2e if not _is_nan(v)]) >= 3 else _nan()

    # Debt-Weighted Profitability (DWP) + slope
    roe_vals = [_convert_to_float(v) for v in prof.get("Return on equity %", {}).values()]
    d2e_vals2 = [_convert_to_float(v) for v in sol.get("Debt to equity ratio", {}).values()]
    valid_roe = [v for v in roe_vals if not _is_nan(v)]
    valid_d2e2 = [v for v in d2e_vals2 if not _is_nan(v)]

    periode = desc.get("Periode", "Vierteljahr")
    period_count = 8 if periode == "Vierteljahr" else 4

    if len(valid_roe) >= 3 and len(valid_d2e2) >= 3:
        roe_last = valid_roe[-period_count:]
        d2e_last = valid_d2e2[-period_count:]
        dwp_vals = []
        for roe, d2e in zip(roe_last, d2e_last):
            try:
                dwp_vals.append(roe / (1 + d2e))
            except (ZeroDivisionError, TypeError):
                dwp_vals.append(_nan())
        valid_dwp = [v for v in dwp_vals if not _is_nan(v)]
        m["Debt-Weighted Profitability"] = valid_dwp[-1] if valid_dwp else _nan()
        m["Slope Debt-Weighted Profitability"] = _calculate_slope(valid_dwp) if len(valid_dwp) >= 3 else _nan()
    else:
        m["Debt-Weighted Profitability"] = _nan()
        m["Slope Debt-Weighted Profitability"] = _nan()

    # Per-share cashflow metrics
    rev_ttm = _get_ttm_sum(per.get("Revenue per share", {}))
    fcf_ttm = _get_ttm_sum(per.get("Free cash flow per share", {}))
    capex_ttm = _get_ttm_sum(per.get("CapEx per share", {}))
    opcf_ttm = _get_ttm_sum(per.get("Operating cash flow per share", {}))

    m["TTM FCF-Marge"] = fcf_ttm / rev_ttm if not _is_nan(rev_ttm) and rev_ttm else _nan()
    m["CapEx / Revenue (TTM)"] = capex_ttm / rev_ttm if not _is_nan(rev_ttm) and rev_ttm else _nan()

    debt_latest = _get_latest_value(per.get("Total debt per share", {}))
    cash_latest = _get_latest_value(per.get("Cash per share", {}))
    net_debt = (debt_latest - cash_latest) if not _is_nan(debt_latest) and not _is_nan(cash_latest) else _nan()
    m["Net Debt / TTM Op. CF"] = net_debt / opcf_ttm if not _is_nan(opcf_ttm) and opcf_ttm else _nan()

    fcf_vals = _get_last_n_values(per.get("Free cash flow per share", {}))
    valid_fcf = [v for v in fcf_vals if not _is_nan(v)]
    if len(valid_fcf) >= 3:
        m["Slope FCF"] = _calculate_slope(valid_fcf) * 4
    else:
        m["Slope FCF"] = _nan()

    if len(valid_fcf) >= 8:
        m["TTM FCF CAGR"] = _calculate_cagr(valid_fcf[-1], valid_fcf[0], 2)
    elif len(valid_fcf) >= 4:
        m["TTM FCF CAGR"] = _calculate_cagr(valid_fcf[-1], valid_fcf[0], len(valid_fcf) / 4)
    else:
        m["TTM FCF CAGR"] = _nan()

    bv_vals = _get_last_n_values(per.get("Book value per share", {}))
    valid_bv = [v for v in bv_vals if not _is_nan(v)]
    m["Book Value Trend"] = _calculate_slope(valid_bv) if len(valid_bv) >= 3 else _nan()

    # Rohwerte für Tabellenanzeige
    m["pe"] = m.get("Price to earnings ratio", _nan())
    m["ev_ebitda"] = m.get("Enterprise value to EBITDA ratio", _nan())
    m["roe"] = m.get("Return on equity %", _nan())
    m["debt_equity"] = m.get("Debt to equity ratio", _nan())
    m["roic"] = m.get("Return on invested capital %", _nan())
    m["revenue_growth"] = _nan()  # nicht direkt in JSON; wird nicht angezeigt

    # Exchange für Anzeige
    m["exchange"] = desc.get("Exchange", "")

    return m


def _has_negative_profitability(m: dict) -> bool:
    for col in _PROFIT_COLS:
        v = m.get(col, _nan())
        if not _is_nan(v) and v < 0:
            return True
    return False


def _all_profit_nan(m: dict) -> bool:
    return all(_is_nan(m.get(col, _nan())) for col in _PROFIT_COLS)


# ── Normalisierung ─────────────────────────────────────────────────────────────

_SCORE_COLS = [k for k in IS_BETTER_HIGHER]


def _minmax_normalize(records: list[dict]) -> None:
    """In-place: fügt für jeden Eintrag in IS_BETTER_HIGHER einen score_{col} hinzu."""
    for col in _SCORE_COLS:
        direction = IS_BETTER_HIGHER[col]
        values = [r[col] for r in records if not _is_nan(r.get(col, _nan()))]
        if len(values) < 3:
            for r in records:
                r[f"score__{col}"] = 0.5
            continue
        mn, mx = min(values), max(values)
        if mx == mn:
            for r in records:
                r[f"score__{col}"] = 0.5
            continue
        for r in records:
            v = r.get(col, _nan())
            if _is_nan(v):
                r[f"score__{col}"] = 0.5
            elif direction:
                r[f"score__{col}"] = max(0.0, min(1.0, (v - mn) / (mx - mn)))
            else:
                r[f"score__{col}"] = max(0.0, min(1.0, (mx - v) / (mx - mn)))


def _safe(r: dict, key: str) -> float:
    v = r.get(key, 0.0)
    return 0.0 if _is_nan(v) else v


def _compute_category_scores(records: list[dict], cfg: dict) -> None:
    """In-place: berechnet die 6 Kategorie-Scores + GesamtScore."""
    pc = cfg["Profitability ratios"]
    sc = cfg["Solvency ratios"]
    cc = cfg.get("Cashflow", {})
    tc = cfg["Trends"]
    vc = cfg["Valuation ratios"]
    lc = cfg["Liquidity ratios"]

    total_v  = vc["total_factor_valuation"]
    total_p  = pc["total_factor_profitability"]
    total_l  = lc["total_factor_liquidity"]
    total_s  = sc["total_factor_solvency"]
    total_c  = cc.get("total_factor_cashflow", 6.6)
    total_t  = tc["total_factor_trends"]
    total_w  = total_v + total_p + total_l + total_s + total_c + total_t

    def s(r, col):
        return _safe(r, f"score__{col}")

    for r in records:
        r["score_valuation"] = (
            s(r, "Price to earnings ratio")          * vc["factor_price_to_earnings"] +
            s(r, "Price to sales ratio")             * vc["factor_price_to_sales"] +
            s(r, "Price to cash flow ratio")         * vc["factor_price_to_cash_flow"] +
            s(r, "Price to book ratio")              * vc["factor_price_to_book"] +
            s(r, "Enterprise value to EBITDA ratio") * vc["factor_enterprise_value_to_ebitda"]
        ) / total_v

        r["score_profitability"] = (
            s(r, "Return on assets %")           * pc["factor_return_on_assets"] +
            s(r, "Return on equity %")           * pc["factor_return_on_equity"] +
            s(r, "Return on invested capital %") * pc["factor_return_on_invested_capital"] +
            s(r, "Gross margin %")               * pc["factor_gross_margin"] +
            s(r, "Operating margin %")           * pc["factor_operating_margin"] +
            s(r, "EBITDA margin %")              * pc["factor_ebitda_margin"] +
            s(r, "Net margin %")                 * pc["factor_net_margin"] +
            s(r, "Debt-Weighted Profitability")  * pc["factor_debt_weighted_profitability"]
        ) / total_p

        r["score_liquidity"] = (
            s(r, "Quick ratio")        * lc["factor_quick_ratio"] +
            s(r, "Current ratio")      * lc["factor_current_ratio"] +
            s(r, "Inventory turnover") * lc["factor_inventory_turnover"] +
            s(r, "Asset turnover")     * lc["factor_asset_turnover"]
        ) / total_l

        r["score_solvency"] = (
            s(r, "Debt to assets ratio")                 * sc["factor_debt_to_assets"] +
            s(r, "Debt to equity ratio")                 * sc["factor_debt_to_equity"] +
            s(r, "Long term debt to total assets ratio") * sc["factor_long_term_debt_to_assets"] +
            s(r, "Long term debt to total equity ratio") * sc["factor_long_term_debt_to_equity"]
        ) / total_s

        r["score_cashflow"] = (
            s(r, "TTM FCF CAGR")           * cc.get("factor_ttm_fcf_cagr", 1.4) +
            s(r, "TTM FCF-Marge")          * cc.get("factor_ttm_fcf_margin", 1.5) +
            s(r, "CapEx / Revenue (TTM)")  * cc.get("factor_capex_to_revenue", 1.3) +
            s(r, "Net Debt / TTM Op. CF")  * cc.get("factor_net_debt_to_opcf", 1.3) +
            s(r, "Book Value Trend")       * cc.get("factor_book_value_trend", 1.1)
        ) / total_c

        r["score_trends"] = (
            s(r, "Slope Gross margin")                    * tc["factor_slope_gross_margin"] +
            s(r, "Slope Debt to equity")                  * tc["factor_slope_debt_to_equity"] +
            s(r, "Slope Debt-Weighted Profitability")     * tc["factor_slope_debt_weighted_profitability"] +
            s(r, "Slope FCF")                             * tc["factor_slope_fcf"] +
            s(r, "Slope Total common shares outstanding") * tc["factor_slope_shares_outstanding"]
        ) / total_t

        r["ranking_score"] = (
            r["score_valuation"]     * total_v +
            r["score_profitability"] * total_p +
            r["score_liquidity"]     * total_l +
            r["score_solvency"]      * total_s +
            r["score_cashflow"]      * total_c +
            r["score_trends"]        * total_t
        ) / total_w


# ── Öffentliche API ────────────────────────────────────────────────────────────

def score_tickers(
    tv_symbols: list[str],
    json_dir: Path,
    config_path: Path,
) -> list[dict]:
    """
    Berechnet Fundamental-Scores für die angegebenen TV-Symbole.

    tv_symbols=[]  → Universe-Modus: alle JSON-Dateien in json_dir
    tv_symbols=[…] → Cluster-Modus: nur diese Ticker, cluster-relativ normalisiert

    Rückgabe: Liste von Dicts, sortiert nach ranking_score (desc), mit Feldern:
      ticker, exchange, cluster_rank, ranking_score,
      score_trends, score_cashflow, score_profitability,
      score_valuation, score_liquidity, score_solvency,
      pe, roe, debt_equity, ev_ebitda, negative (bool)
    """
    with open(config_path, encoding="utf-8-sig") as f:
        cfg = json.load(f)

    # Ticker→Exchange-Mapping aus TV-Symbolen
    sym_map: dict[str, str] = {}  # ticker → exchange
    if tv_symbols:
        for sym in tv_symbols:
            if ":" in sym:
                exc, tick = sym.split(":", 1)
            else:
                tick, exc = sym, ""
            sym_map[tick.upper()] = exc.upper()

    # JSON-Dateien bestimmen
    if tv_symbols:
        files: list[tuple[str, str, Path]] = []  # (ticker, exchange, path)
        for tick, exc in sym_map.items():
            p = _json_path(tick, exc, json_dir)
            if p.exists():
                files.append((tick, exc, p))
            else:
                log.warning("JSON nicht gefunden: %s", p)
    else:
        files = []
        for p in sorted(json_dir.glob("financial_data_*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                tick = d.get("Descriptive Data", {}).get("Ticker", p.stem)
                exc = d.get("Descriptive Data", {}).get("Exchange", "")
                files.append((tick, exc, p))
            except Exception:
                continue

    # Kennzahlen extrahieren
    records: list[dict] = []
    negative: list[dict] = []

    for tick, exc, p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("JSON-Lesefehler %s: %s", p, e)
            continue
        m = _extract(data, tick)
        if exc:
            m["exchange"] = exc
        if _all_profit_nan(m):
            continue
        if _has_negative_profitability(m):
            m["negative"] = True
            negative.append(m)
        else:
            m["negative"] = False
            records.append(m)

    if not records and not negative:
        return []

    # Normalisieren (nur positive records, wie Original)
    _minmax_normalize(records)
    _compute_category_scores(records, cfg)

    # Sortieren + cluster_rank vergeben
    records.sort(key=lambda r: r.get("ranking_score", 0.0), reverse=True)
    for i, r in enumerate(records, 1):
        r["cluster_rank"] = i

    # Negative ans Ende (ohne Scores)
    for r in negative:
        r["ranking_score"] = _nan()
        r["score_trends"] = _nan()
        r["score_cashflow"] = _nan()
        r["score_profitability"] = _nan()
        r["score_valuation"] = _nan()
        r["score_liquidity"] = _nan()
        r["score_solvency"] = _nan()
        r["cluster_rank"] = None

    # Interne Score-Spalten bereinigen
    all_out = records + negative
    clean_keys = {
        "ticker", "exchange", "cluster_rank", "ranking_score", "negative",
        "score_trends", "score_cashflow", "score_profitability",
        "score_valuation", "score_liquidity", "score_solvency",
        "pe", "ev_ebitda", "roe", "debt_equity", "roic", "revenue_growth",
    }
    return [{k: v for k, v in r.items() if k in clean_keys} for r in all_out]


def write_to_db_sync(records: list[dict], pool_dsn: str) -> int:
    """
    Synchroner DB-Schreiber für BackgroundTask/CLI.
    Nutzt asyncpg (bereits Abhängigkeit des Dashboards).
    """
    import asyncio
    import asyncpg

    def _f(v) -> float | None:
        return None if _is_nan(v) else float(v)

    today = date.today()
    rows = []
    for r in records:
        ticker = r.get("ticker", "")
        if not ticker:
            continue
        rows.append((
            ticker, today, "tv",
            r.get("exchange", ""),
            _f(r.get("pe")), _f(r.get("ev_ebitda")), _f(r.get("roe")),
            _f(r.get("debt_equity")), _f(r.get("revenue_growth")),
            _f(r.get("ranking_score")), r.get("cluster_rank"),
            _f(r.get("score_trends")), _f(r.get("score_cashflow")),
            _f(r.get("score_profitability")), _f(r.get("score_valuation")),
            _f(r.get("score_liquidity")), _f(r.get("score_solvency")),
            _f(r.get("roic")),
        ))
    if not rows:
        return 0

    sql = """
        INSERT INTO fundamentals
          (ticker, updated, source, exchange, pe, ev_ebitda, roe, debt_equity, revenue_growth,
           ranking_score, ranking_pos, score_trends, score_cashflow, score_profitability,
           score_valuation, score_liquidity, score_solvency, roic)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
        ON CONFLICT (ticker, updated, source) DO UPDATE SET
          exchange=EXCLUDED.exchange,
          pe=EXCLUDED.pe, ev_ebitda=EXCLUDED.ev_ebitda, roe=EXCLUDED.roe,
          debt_equity=EXCLUDED.debt_equity, ranking_score=EXCLUDED.ranking_score,
          ranking_pos=EXCLUDED.ranking_pos, score_trends=EXCLUDED.score_trends,
          score_cashflow=EXCLUDED.score_cashflow,
          score_profitability=EXCLUDED.score_profitability,
          score_valuation=EXCLUDED.score_valuation, score_liquidity=EXCLUDED.score_liquidity,
          score_solvency=EXCLUDED.score_solvency, roic=EXCLUDED.roic
    """

    async def _run():
        con = await asyncpg.connect(pool_dsn)
        try:
            await con.executemany(sql, rows)
        finally:
            await con.close()

    asyncio.run(_run())
    return len(rows)
