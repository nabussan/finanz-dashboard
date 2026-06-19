"""
Gemeinsame CRUD-Logik für watchlist.py und portfolio_lists.py.

watchlists und portfolio_lists sind technisch dasselbe (benannte Ticker-Gruppen),
unterschieden nur über clusters.kind ('watchlist' | 'portfolio_list'). Siehe
/home/christoph/Finanz/schema.sql.
"""
import re
import subprocess

_SYMBOL_RE = re.compile(r'^[A-Z0-9]+:[A-Z0-9.]+$')


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


async def insert_items(pool, cluster_id: int, symbols: list[str]) -> None:
    if not symbols:
        return
    await pool.executemany(
        "INSERT INTO cluster_items (cluster_id, tv_symbol) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        [(cluster_id, sym) for sym in symbols],
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
