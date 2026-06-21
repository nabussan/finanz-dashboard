"""
Gemeinsame CRUD-Logik für watchlist.py und portfolio_lists.py.

watchlists und portfolio_lists sind technisch dasselbe (benannte Ticker-Gruppen),
unterschieden nur über clusters.kind ('watchlist' | 'portfolio_list'). Siehe
/home/christoph/Finanz/schema.sql.
"""
import re
import subprocess

_SYMBOL_RE = re.compile(r'^[A-Z0-9]+:[A-Z0-9.]+$')

# Exchanges, die nachweislich ohne Sonderbehandlung ueber IBKR aufloesbar sind --
# Vereinigung aus rsm-live/lib/ibkr_fetcher.py:_TV_TO_IBKR_EXCH (kuratierte Eintraege)
# und den am 2026-06-18 gegen das echte Gateway verifizierten Passthrough-Boersen
# (siehe rsm_live_project-Memory "Exchange-Mapping fuer nicht-US-Boersen vervollstaendigt").
# Manuell synchron halten -- kein Cross-Repo-Import zwischen finanz-dashboard und rsm-live.
_KNOWN_IBKR_EXCHANGES = {
    "NYSE", "NASDAQ", "AMEX", "ARCA",
    "XETR", "TSX", "TSXV", "HKEX", "OTC",
    "LSE", "EURONEXT", "ASX", "SIX", "OMX", "KRX",
}


def classify_ibkr_coverage(tv_symbol: str) -> str:
    """'resolved' | 'unresolved' -- Format- + kuratierter Exchange-Check, kein Live-IBKR-Call.

    Bewusst kein reqContractDetails()-Aufruf beim Upload (keine Gateway-Abhaengigkeit,
    keine Latenz bei grossen Listen). Falsch-positive 'resolved'-Eintraege (Exchange
    bekannt, aber der Ticker existiert dort nicht) fallen beim naechsten eod_update.py-
    Lauf als Fetch-Fehler auf.
    """
    if not _SYMBOL_RE.match(tv_symbol):
        return "unresolved"
    exch = tv_symbol.split(":", 1)[0]
    return "resolved" if exch in _KNOWN_IBKR_EXCHANGES else "unresolved"


def parse_tv_import(content: str) -> list[str]:
    """Parse comma- or newline-separated TV symbols (EXCHANGE:TICKER)."""
    symbols = []
    for part in re.split(r'[,\n\r]+', content):
        part = part.strip()
        if not part or part.startswith('#'):
            continue
        if ':' in part and _SYMBOL_RE.match(part):
            symbols.append(part)
    return list(dict.fromkeys(symbols))  # deduplicate, preserve order


def parse_micro_import(content: str) -> list[str]:
    """Wie parse_tv_import(), behaelt aber auch fehlerhafte Eintraege (kein Doppelpunkt,
    falsches Format) statt sie stillschweigend zu verwerfen -- micro-Listen sollen
    Format-Fehler sichtbar machen (Lehre aus dem RMV_LSE-Bug: ein stillschweigend
    verworfener oder durchgerutschter Ticker faellt sonst erst beim Chart-Klick auf)."""
    parts = []
    for part in re.split(r'[,\n\r]+', content):
        part = part.strip()
        if not part or part.startswith('#'):
            continue
        parts.append(part)
    return list(dict.fromkeys(parts))  # deduplicate, preserve order


async def upsert_cluster(pool, name: str, kind: str) -> int:
    """Create cluster if not exists, return id."""
    row = await pool.fetchrow(
        "INSERT INTO clusters (name, kind) VALUES ($1, $2) "
        "ON CONFLICT (name, kind) DO NOTHING RETURNING id",
        name, kind,
    )
    return row["id"] if row else await pool.fetchval(
        "SELECT id FROM clusters WHERE name = $1 AND kind = $2", name, kind
    )


async def insert_items(pool, cluster_id: int, symbols: list[str], statuses: list[str] | None = None) -> None:
    """statuses: optionale Liste (gleiche Laenge wie symbols) mit ibkr_status je Ticker
    (z.B. 'resolved'/'unresolved', siehe classify_ibkr_coverage()) -- nur fuer micro-Listen
    genutzt, watchlist.py/portfolio_lists.py rufen ohne statuses auf (unveraendertes Verhalten)."""
    if not symbols:
        return
    if statuses is None:
        await pool.executemany(
            "INSERT INTO cluster_items (cluster_id, tv_symbol) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            [(cluster_id, sym) for sym in symbols],
        )
    else:
        await pool.executemany(
            "INSERT INTO cluster_items (cluster_id, tv_symbol, ibkr_status) VALUES ($1, $2, $3) "
            "ON CONFLICT (cluster_id, tv_symbol) DO UPDATE SET ibkr_status = EXCLUDED.ibkr_status",
            [(cluster_id, sym, st) for sym, st in zip(symbols, statuses)],
        )


async def tickers_missing_prices(pool, cluster_id: int) -> list[str]:
    """Cluster-Ticker ohne Weekly- oder Daily-Kursdaten in rsm_prices."""
    rows = await pool.fetch(
        """
        SELECT ci.tv_symbol FROM cluster_items ci
        WHERE ci.cluster_id = $1
        AND (
            NOT EXISTS (SELECT 1 FROM rsm_prices WHERE ticker = ci.tv_symbol AND interval = '1week')
            OR NOT EXISTS (SELECT 1 FROM rsm_prices WHERE ticker = ci.tv_symbol AND interval = '1day')
        )
        """,
        cluster_id,
    )
    return [r["tv_symbol"] for r in rows]


def trigger_ondemand_update() -> None:
    """Feuert den Sofort-Update-Lauf an (fire-and-forget, eigener Lock+Client-IDs)."""
    subprocess.Popen(
        ["/opt/rsm-live/infra/ondemand_update.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def trigger_reclassify(tv_symbols: list[str]) -> None:
    """Ad-hoc Klasse-Neuberechnung fuer die gegebenen Ticker (fire-and-forget).

    Reiner Lokal-Backtest gegen vorhandene Kursdaten in rsm-live/data/rsm_data.db,
    kein IBKR/Netzwerk-Zugriff -- anders als trigger_ondemand_update() also ohne
    eigenes flock-Lock noetig (determine_class.py haelt keine IBKR-Verbindung).
    """
    if not tv_symbols:
        return
    subprocess.Popen(
        ["/opt/rsm-live/.venv/bin/python3", "src/determine_class.py",
         "--ticker", *tv_symbols],
        cwd="/opt/rsm-live",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def delete_item(pool, cluster_id: int, tv_symbol: str) -> None:
    await pool.execute(
        "DELETE FROM cluster_items WHERE cluster_id = $1 AND tv_symbol = $2",
        cluster_id, tv_symbol,
    )


async def delete_cluster(pool, cluster_id: int) -> None:
    await pool.execute("DELETE FROM clusters WHERE id = $1", cluster_id)
