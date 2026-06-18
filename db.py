"""asyncpg connection pool — shared across the app lifecycle."""
import asyncpg
from config import DB_URL

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=10)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_price_update_times(pool) -> dict:
    """Letzte Intraday- und EOD-Aktualisierungszeitstempel aus price_updates."""
    try:
        rows = await pool.fetch(
            "SELECT run_type, ts AT TIME ZONE 'Europe/Berlin' AS ts, n_tickers "
            "FROM price_updates ORDER BY run_type"
        )
        return {r["run_type"]: r for r in rows}
    except Exception:
        return {}
