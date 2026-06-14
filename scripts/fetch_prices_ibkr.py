"""
Prüft welche Ticker in 'fundamentals' noch keine Wochenkurs-Daten in 'prices' haben
und ruft diese von IBKR ab (reqHistoricalData, TRADES, 1 week).

Aufruf:
  python scripts/fetch_prices_ibkr.py           # alle fehlenden Ticker
  python scripts/fetch_prices_ibkr.py --all     # alle neu laden (inkl. bestehende)
  python scripts/fetch_prices_ibkr.py AAPL MSFT # nur diese Ticker

Rate-Limit: IBKR erlaubt ~50 req/10s; _SLEEP = 0.3s zwischen Requests ist sicher.
Für 874 Ticker: ca. 4-5 Minuten.
"""
import sys
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import asyncio
import logging
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(_ROOT).parent / ".env")

import asyncpg
import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_SLEEP = 0.35        # Sekunden zwischen IBKR-Requests
_DURATION = "5 Y"   # Historien-Tiefe
_BAR_SIZE = "1 week"

# TV-Exchange → IBKR-Exchange (Mapping aus rsm-live/lib/ibkr_fetcher.py)
_TV_TO_IBKR: dict[str, str] = {
    "XETR": "IBIS",
    "AMEX": "ARCA",
}
_EXCH_CURRENCY: dict[str, str] = {
    "XETR": "EUR", "FWB": "EUR", "EURONEXT": "EUR",
    "LSE":  "GBP", "ASX": "AUD", "TSX": "CAD", "TSXV": "CAD",
    "IBIS": "EUR",
}


def _make_contract(ticker: str, tv_exchange: str):
    from ib_insync import Stock
    ibkr_exch = _TV_TO_IBKR.get(tv_exchange, tv_exchange)
    currency   = _EXCH_CURRENCY.get(tv_exchange, "USD")
    return Stock(ticker, "SMART", currency, primaryExchange=ibkr_exch)


async def _get_missing(dsn: str, force_all: bool, explicit: list[str]) -> list[tuple[str, str]]:
    """Gibt Liste von (ticker, exchange) zurück, für die keine Preisdaten vorhanden sind."""
    con = await asyncpg.connect(dsn)
    try:
        if explicit:
            rows = await con.fetch(
                "SELECT DISTINCT ticker, exchange FROM fundamentals WHERE ticker = ANY($1)",
                explicit,
            )
        elif force_all:
            rows = await con.fetch(
                "SELECT DISTINCT ticker, exchange FROM fundamentals ORDER BY ticker"
            )
        else:
            rows = await con.fetch(
                """
                SELECT DISTINCT f.ticker, f.exchange
                FROM fundamentals f
                WHERE NOT EXISTS (
                    SELECT 1 FROM prices p
                    WHERE p.ticker = f.ticker AND p.exchange = f.exchange
                )
                ORDER BY f.ticker
                """
            )
        return [(r["ticker"], r["exchange"]) for r in rows]
    finally:
        await con.close()


async def _write_bars(dsn: str, ticker: str, exchange: str, bars) -> int:
    if not bars:
        return 0
    rows = [
        (ticker, exchange, str(bar.date)[:10],
         float(bar.open), float(bar.high), float(bar.low), float(bar.close),
         int(bar.volume) if bar.volume else None)
        for bar in bars
        if bar.close
    ]
    if not rows:
        return 0
    sql = """
        INSERT INTO prices (ticker, exchange, date, open, high, low, close, volume)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (ticker, exchange, date) DO UPDATE SET
          open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
          close=EXCLUDED.close, volume=EXCLUDED.volume
    """
    con = await asyncpg.connect(dsn)
    try:
        await con.executemany(sql, rows)
    finally:
        await con.close()
    return len(rows)


def main():
    force_all = "--all" in sys.argv
    explicit  = [a for a in sys.argv[1:] if not a.startswith("-")]

    missing = asyncio.run(_get_missing(config.DB_URL, force_all, explicit))
    if not missing:
        log.info("Keine fehlenden Ticker. Fertig.")
        return

    log.info("Fehlende Ticker: %d — starte IBKR-Abruf", len(missing))

    # IBKR verbinden (aus rsm-live ibkr_fetcher importieren)
    rsm_lib = Path(_ROOT).parent / "rsm-live" / "lib"
    sys.path.insert(0, str(rsm_lib))
    try:
        import ibkr_fetcher
    except ImportError:
        log.error("ibkr_fetcher nicht gefunden unter %s", rsm_lib)
        sys.exit(1)

    ib = ibkr_fetcher.connect(client_id_override=13)  # eigene Client-ID, kein Konflikt

    ok = 0
    skip = 0
    err = 0
    for i, (ticker, exchange) in enumerate(missing, 1):
        try:
            contract = _make_contract(ticker, exchange)
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=_DURATION,
                barSizeSetting=_BAR_SIZE,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            time.sleep(_SLEEP)

            if not bars:
                log.debug("Keine Bars: %s (%s)", ticker, exchange)
                skip += 1
                continue

            n = asyncio.run(_write_bars(config.DB_URL, ticker, exchange, bars))
            log.info("[%d/%d] %s:%s → %d Wochen", i, len(missing), exchange, ticker, n)
            ok += 1

        except Exception as e:
            log.warning("[%d/%d] Fehler %s:%s — %s", i, len(missing), exchange, ticker, e)
            err += 1
            time.sleep(1)

    ibkr_fetcher.disconnect(ib)
    log.info("Fertig: %d OK, %d ohne Daten, %d Fehler", ok, skip, err)


if __name__ == "__main__":
    main()
