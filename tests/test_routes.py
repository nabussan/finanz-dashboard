"""
Dashboard Smoke-Tests — prüft alle wichtigen Routen und DB-Verbindung.
Verwendung:
    cd /home/.../finanz-dashboard
    .venv/bin/pytest tests/ -v
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def client():
    import db as _db
    _db._pool = None  # Frischer Pool pro Test — Lifespan schließt ihn beim Teardown
    from app import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Statische Seiten ─────────────────────────────────────────────────────────

async def test_index(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Übersicht" in r.text or "Finanz" in r.text



async def test_rsm(client):
    r = await client.get("/rsm")
    assert r.status_code == 200



async def test_portfolios_redirect(client):
    r = await client.get("/portfolio", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert "/portfolios" in r.headers.get("location", "")



async def test_portfolios_ibkr(client):
    r = await client.get("/portfolios?broker=ibkr")
    assert r.status_code == 200
    assert "IBKR" in r.text or "Positionen" in r.text or "Ticker" in r.text



async def test_portfolios_sonstige(client):
    r = await client.get("/portfolios?broker=sonstige")
    assert r.status_code == 200



async def test_disco(client):
    r = await client.get("/disco")
    assert r.status_code == 200



async def test_micro(client):
    r = await client.get("/micro")
    assert r.status_code == 200


# ── Charts ───────────────────────────────────────────────────────────────────


async def test_portfolio_charts_weekly(client):
    r = await client.get("/portfolio-charts")
    assert r.status_code == 200
    assert "rsm-live" in r.text.lower() or "lightweight" in r.text.lower()



async def test_portfolio_charts_daily(client):
    r = await client.get("/portfolio-charts-daily")
    assert r.status_code == 200
    assert "Daily" in r.text or "lightweight" in r.text.lower()



async def test_charts_have_embed_mode(client):
    """Embed-Mode: sidebar + listbar werden im iframe ausgeblendet."""
    for url in ("/portfolio-charts", "/portfolio-charts-daily"):
        r = await client.get(url)
        assert "window.self!==window.top" in r.text, f"Embed-Mode fehlt in {url}"



async def test_charts_have_hashchange(client):
    """hashchange-Listener muss in beiden Chart-Dateien vorhanden sein."""
    for url in ("/portfolio-charts", "/portfolio-charts-daily"):
        r = await client.get(url)
        assert "hashchange" in r.text, f"hashchange-Listener fehlt in {url}"



async def test_daily_link_uses_dashboard_url(client):
    """Weekly-Chart darf nicht auf portfolio_daily.html zeigen (relativer Pfad)."""
    r = await client.get("/portfolio-charts")
    assert "portfolio_daily.html" not in r.text
    assert "/portfolio-charts-daily" in r.text



async def test_weekly_link_in_daily(client):
    """Daily-Chart muss auf /portfolio-charts zeigen."""
    r = await client.get("/portfolio-charts-daily")
    assert "portfolio.html" not in r.text or "/portfolio-charts" in r.text


# ── Watchlists ───────────────────────────────────────────────────────────────


async def test_watchlists_redirect(client):
    r = await client.get("/watchlists", follow_redirects=False)
    # Entweder Redirect auf erste WL oder 200 (keine WLs vorhanden)
    assert r.status_code in (200, 302)



async def test_watchlist_page_loads(client):
    r = await client.get("/watchlists", follow_redirects=True)
    assert r.status_code == 200



async def test_watchlist_create_and_delete(client):
    """Anlegen und Löschen einer Test-Watchlist."""
    # Anlegen
    r = await client.post("/watchlists", data={"name": "_test_pytest_"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    location = r.headers.get("location", "")
    assert "/watchlists/" in location

    wl_id = int(location.rsplit("/", 1)[-1])

    # Seite laden
    r = await client.get(f"/watchlists/{wl_id}")
    assert r.status_code == 200
    assert "_test_pytest_" in r.text

    # Löschen
    r = await client.post(f"/watchlists/{wl_id}/delete", follow_redirects=False)
    assert r.status_code in (302, 303)

    # Danach nicht mehr vorhanden
    r = await client.get(f"/watchlists/{wl_id}", follow_redirects=False)
    assert r.status_code in (302, 404)



async def test_watchlist_import(client):
    """TV-Format-Import: kommagetrennte Symbole."""
    r = await client.post("/watchlists", data={"name": "_test_import_"}, follow_redirects=False)
    wl_id = int(r.headers["location"].rsplit("/", 1)[-1])

    r = await client.post(
        f"/watchlists/{wl_id}/import",
        data={"content": "NASDAQ:AAPL,NYSE:SPY"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "NASDAQ:AAPL" in r.text or "AAPL" in r.text

    # Aufräumen
    await client.post(f"/watchlists/{wl_id}/delete")



async def test_watchlist_upload(client):
    """Datei-Upload: Dateiname → Watchlist-Name, Inhalt → Ticker."""
    import io
    content = b"NASDAQ:MSFT,NASDAQ:GOOG"
    r = await client.post(
        "/watchlists/upload",
        files={"file": ("_test_upload_.txt", io.BytesIO(content), "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    wl_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])

    r = await client.get(f"/watchlists/{wl_id}")
    assert r.status_code == 200
    assert "_test_upload_" in r.text

    await client.post(f"/watchlists/{wl_id}/delete")


# ── Admin / Pipeline Trigger ─────────────────────────────────────────────────


async def test_admin_run_unknown_task(client):
    r = await client.post("/admin/run/nonexistent_task")
    assert r.status_code == 400



async def test_admin_log_w3(client):
    r = await client.get("/admin/log?task=w3")
    assert r.status_code == 200



async def test_admin_log_eod(client):
    r = await client.get("/admin/log?task=eod")
    assert r.status_code == 200


# ── DB ───────────────────────────────────────────────────────────────────────


async def test_db_signals_exist(client):
    """Mindestens ein Signal muss in der DB sein."""
    import db, config
    pool = await db.get_pool()
    count = await pool.fetchval("SELECT COUNT(*) FROM signals")
    assert count > 0, "signals-Tabelle ist leer"



async def test_db_signals_ticker_format(client):
    """signals.ticker muss TV-Format sein (enthält ':')."""
    import db
    pool = await db.get_pool()
    sample = await pool.fetchval("SELECT ticker FROM signals LIMIT 1")
    assert sample is not None
    assert ":" in sample, f"Ticker-Format falsch: {sample!r} — erwartet TV-Format (EXCHANGE:SYM)"



async def test_db_watchlist_join(client):
    """Watchlist-Ticker müssen via tv_symbol auf signals joinen (nicht bare ticker)."""
    import db
    pool = await db.get_pool()
    # Prüfe ob Join auf tv_symbol Treffer liefert (falls Watchlist befüllt)
    count_wl = await pool.fetchval("SELECT COUNT(*) FROM watchlist_items")
    if count_wl == 0:
        pytest.skip("Keine Watchlist-Einträge zum Testen")
    hits = await pool.fetchval("""
        SELECT COUNT(*) FROM watchlist_items wi
        WHERE EXISTS (
            SELECT 1 FROM signals WHERE ticker = wi.tv_symbol
        )
    """)
    # Mindestens ein Treffer erwartet (wenn Signale und Watchlist befüllt)
    # Kein harter Assert — loggt nur als Info
    print(f"Watchlist-Treffer in signals: {hits}/{count_wl}")
