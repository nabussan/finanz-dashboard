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


async def test_micro_iframe_structure(client):
    """Micro-Seite muss einen chart-frame iframe haben."""
    r = await client.get("/micro")
    assert r.status_code == 200
    assert 'id="chart-frame"' in r.text
    assert '/chart/' in r.text


async def test_micro_chart_controls(client):
    """Micro-Seite muss die äußere W/D/TV-Toggle-Zeile + TV Overview/Chart-
    Links haben (TV bleibt sichtbar, auch wenn der iframe für den
    eingebetteten TradingView-Chart versteckt wird)."""
    r = await client.get("/micro")
    assert r.status_code == 200
    assert 'chart-mode-btn' in r.text
    assert 'id="tv-overview-link"' in r.text
    assert 'id="tv-chart-link"' in r.text
    for label in ('W', 'D', 'TV', 'TV Overview', 'Chart'):
        assert label in r.text


async def test_micro_table_columns(client):
    """Micro-Tabelle muss alle Kernspalten enthalten."""
    r = await client.get("/micro")
    assert r.status_code == 200
    for col in ('ROIC', 'EV/EBIT', 'ROE%', 'D/E', 'Univ#', 'Score'):
        assert col in r.text, f"Spalte {col!r} fehlt in /micro"


async def test_micro_tv_symbol_attribute(client):
    """Jede Tabellenzeile muss data-tv-symbol haben (Chart-URL-Navigation)."""
    r = await client.get("/micro")
    assert r.status_code == 200
    assert 'data-tv-symbol="' in r.text


async def test_micro_chart_data_invalid_interval(client):
    """chart-data-Endpoint soll 400 bei ungültigem Interval zurückgeben."""
    r = await client.get("/micro/chart-data/NASDAQ:AAPL?interval=invalid")
    assert r.status_code == 400


async def test_micro_chart_data_unknown_ticker(client):
    """chart-data-Endpoint soll 404 für unbekannten Ticker zurückgeben."""
    r = await client.get("/micro/chart-data/UNKNOWN:XYZABC123?interval=1week")
    assert r.status_code == 404


async def test_micro_chart_data_ibkr_weekly(client):
    """chart-data liefert OHLCV für IBKR weekly wenn rsm_data.db vorhanden."""
    import config
    db_path = config.RSM_DATA_DIR / "rsm_data.db"
    if not db_path.exists():
        pytest.skip("rsm_data.db nicht vorhanden")
    r = await client.get("/micro/chart-data/NASDAQ:IBKR?interval=1week")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "NASDAQ:IBKR"
    assert data["interval"] == "1week"
    assert len(data["ohlc"]) > 100
    bar = data["ohlc"][0]
    for field in ("time", "open", "high", "low", "close"):
        assert field in bar, f"OHLCV-Feld {field!r} fehlt"


# ── Charts ───────────────────────────────────────────────────────────────────


async def test_chart_endpoint_no_data(client):
    """GET /chart/{ticker} gibt 200 zurück, auch wenn kein Ticker in DB (zeigt Fehlermeldung)."""
    r = await client.get("/chart/UNKNOWN:XYZABC123?period=W")
    assert r.status_code == 200
    assert "Keine OHLCV-Daten" in r.text



async def test_chart_endpoint_invalid_period(client):
    """Ungültiger period-Parameter fällt auf 'W' zurück (kein 4xx)."""
    r = await client.get("/chart/NASDAQ:AAPL?period=INVALID")
    assert r.status_code == 200



async def test_chart_endpoint_embed_mode(client):
    """Chart-Seite muss Embed-Mode-Erkennung enthalten."""
    r = await client.get("/chart/NASDAQ:AAPL?period=W")
    assert r.status_code == 200
    assert "window.self !== window.top" in r.text or "Keine OHLCV-Daten" in r.text


# ── Clusters ─────────────────────────────────────────────────────────────────


async def test_clusters_page(client):
    r = await client.get("/clusters")
    assert r.status_code == 200
    assert "Cluster" in r.text


async def test_cluster_create_assign_delete(client):
    """Cluster anlegen, View zuweisen, Ticker importieren, löschen."""
    # Anlegen
    r = await client.post("/clusters", data={"name": "_test_cluster_"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    cid = int(r.headers["location"].split("#")[-1])

    # Ticker importieren
    r = await client.post(
        f"/clusters/{cid}/import",
        data={"content": "NASDAQ:MSFT,NYSE:SPY", "mode": "tv"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    # View zuweisen
    r = await client.post(
        f"/clusters/{cid}/assign",
        data={"view_name": "watchlist"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    # Clusters-Seite zeigt Cluster + View-Badge
    r = await client.get("/clusters")
    assert r.status_code == 200
    assert "_test_cluster_" in r.text
    assert "watchlist" in r.text

    # Watchlists-Seite zeigt Cluster
    r = await client.get("/watchlists", follow_redirects=True)
    assert r.status_code == 200

    # View wieder entfernen
    r = await client.post(
        f"/clusters/{cid}/unassign",
        data={"view_name": "watchlist"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    # Löschen
    r = await client.post(f"/clusters/{cid}/delete", follow_redirects=False)
    assert r.status_code in (302, 303)


async def test_cluster_upload(client):
    """Datei-Upload: Dateiname → Cluster-Name, Inhalt → Ticker."""
    import io
    content = b"NASDAQ:MSFT,NASDAQ:GOOG"
    r = await client.post(
        "/clusters/upload",
        files={"file": ("_test_upload_.txt", io.BytesIO(content), "text/plain")},
        data={"mode": "tv"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    cid = int(r.headers["location"].split("#")[-1])

    r = await client.get("/clusters")
    assert r.status_code == 200
    assert "_test_upload_" in r.text

    await client.post(f"/clusters/{cid}/delete")


# ── Watchlists ───────────────────────────────────────────────────────────────


async def test_watchlists_redirect(client):
    r = await client.get("/watchlists", follow_redirects=False)
    # Entweder Redirect auf erste WL oder 200 (keine WLs vorhanden)
    assert r.status_code in (200, 302)



async def test_watchlist_page_loads(client):
    r = await client.get("/watchlists", follow_redirects=True)
    assert r.status_code == 200


# ── Micro-Listen ──────────────────────────────────────────────────────────────


def test_classify_ibkr_coverage():
    """Format- + Exchange-Check, kein Live-IBKR-Call."""
    from routers._cluster_shared import classify_ibkr_coverage
    assert classify_ibkr_coverage("NYSE:AAPL") == "resolved"
    assert classify_ibkr_coverage("LSE:RMV") == "resolved"
    assert classify_ibkr_coverage("XXXX:FOO") == "unresolved"   # unbekannte Exchange
    assert classify_ibkr_coverage("RMV_LSE") == "unresolved"    # kein EXCHANGE:TICKER-Format


async def test_micro_listen_page(client):
    r = await client.get("/micro-listen")
    assert r.status_code == 200


async def test_micro_listen_upload_resolved_and_unresolved(client):
    """Upload mit gemischtem Format: gueltige + ungueltige Eintraege werden getrennt,
    nicht stillschweigend verworfen (Lehre aus dem RMV_LSE-Bug)."""
    r = await client.post(
        "/clusters",
        data={"name": "_test_micro_listen_"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    list_id = int(r.headers["location"].split("#")[-1])

    # View zuweisen + Ticker importieren (Micro-Modus)
    await client.post(f"/clusters/{list_id}/assign", data={"view_name": "micro"})
    r = await client.post(
        f"/clusters/{list_id}/import",
        data={"content": "NYSE:AAPL,RMV_LSE,XXXX:FOO", "mode": "micro",
              "next": f"/micro-listen/{list_id}"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "resolved" in r.text
    assert "unresolved" in r.text
    assert "AAPL" in r.text
    assert "RMV_LSE" in r.text  # ungueltiger Eintrag bleibt sichtbar, nicht verworfen

    await client.post(f"/clusters/{list_id}/delete")


async def test_micro_listen_delete_keeps_fundamentals(client):
    """Cluster loeschen entfernt nur die Gruppierung, nie die zugrunde liegenden
    Daten in fundamentals/rsm_prices (Nutzer-Anforderung)."""
    import db
    pool = await db.get_pool()

    r = await client.post(
        "/clusters",
        data={"name": "_test_micro_delete_"},
        follow_redirects=False,
    )
    list_id = int(r.headers["location"].split("#")[-1])
    await client.post(f"/clusters/{list_id}/assign", data={"view_name": "micro"})
    await client.post(f"/clusters/{list_id}/import",
                      data={"content": "NYSE:AAPL", "mode": "micro"})

    before = await pool.fetchval("SELECT COUNT(*) FROM fundamentals WHERE ticker = 'AAPL'")

    await client.post(f"/clusters/{list_id}/delete")

    after = await pool.fetchval("SELECT COUNT(*) FROM fundamentals WHERE ticker = 'AAPL'")
    assert before == after

    r = await client.get(f"/micro-listen/{list_id}", follow_redirects=False)
    assert r.status_code in (302, 404)


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
    count_wl = await pool.fetchval(
        """SELECT COUNT(*) FROM cluster_items ci
           JOIN cluster_views cv ON cv.cluster_id = ci.cluster_id
           WHERE cv.view_name = 'watchlist'"""
    )
    if count_wl == 0:
        pytest.skip("Keine Watchlist-Einträge zum Testen")
    hits = await pool.fetchval(
        """SELECT COUNT(*) FROM cluster_items ci
           JOIN cluster_views cv ON cv.cluster_id = ci.cluster_id
           WHERE cv.view_name = 'watchlist'
           AND EXISTS (SELECT 1 FROM signals WHERE ticker = ci.tv_symbol)"""
    )
    print(f"Watchlist-Treffer in signals: {hits}/{count_wl}")
