-- Finanz Dashboard — PostgreSQL Schema
-- Anwendung: psql -U finanz -d finanz -f schema.sql
-- Setup:
--   sudo -u postgres createuser finanz --pwprompt
--   sudo -u postgres createdb finanz --owner=finanz

-- ─── RSM-Live ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS rsm_prices (
    ticker   TEXT    NOT NULL,
    interval TEXT    NOT NULL,  -- '1week', '1day'
    date     DATE    NOT NULL,
    open     NUMERIC,
    high     NUMERIC,
    low      NUMERIC,
    close    NUMERIC,
    volume   BIGINT,
    PRIMARY KEY (ticker, interval, date)
);

CREATE TABLE IF NOT EXISTS rsm_tickers (
    ticker        TEXT PRIMARY KEY,
    exchange      TEXT,
    klasse        TEXT,   -- 'A', 'B', 'C', 'D'
    benchmark     TEXT,
    last_w_update DATE,
    last_d_update DATE,
    added_date    DATE,
    td_available  BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS options_data (
    ticker      TEXT NOT NULL,
    date        DATE NOT NULL,
    iv_rank     NUMERIC,
    hv          NUMERIC,
    vrp         NUMERIC,
    ann_return  NUMERIC,
    earnings_dt DATE,
    updated_at  TIMESTAMPTZ,
    PRIMARY KEY (ticker, date)
);

-- Computed results nach jedem run_w3.py-Run
CREATE TABLE IF NOT EXISTS signals (
    id         SERIAL  PRIMARY KEY,
    ticker     TEXT    NOT NULL,
    run_date   DATE    NOT NULL DEFAULT CURRENT_DATE,
    signal     TEXT,           -- 'Buy', 'Sell', 'Hold', 'Wait'
    score      NUMERIC,        -- 0–100
    z_score    NUMERIC,        -- W3 z-slope
    iv_rank    NUMERIC,
    vrp        NUMERIC,
    ann_return NUMERIC,
    klasse     TEXT,           -- 'A', 'B', 'C', 'D'
    klasse_updated DATE,       -- wann determine_class.py die Klasse zuletzt gesetzt hat
    run_at     TIMESTAMPTZ DEFAULT NOW(),  -- Uhrzeit des run_w3.py-Laufs (run_date hat nur Tagesaufloesung)
    UNIQUE (ticker, run_date)  -- ein Signal pro Ticker+Tag, run_w3.py upserted
);
CREATE INDEX IF NOT EXISTS signals_run_date_idx ON signals(run_date DESC);
CREATE INDEX IF NOT EXISTS signals_ticker_idx   ON signals(ticker);

-- ─── Portfolio ───────────────────────────────────────────────────────────────

-- Input: IBKR live (ibkr_portfolio.py) + Excel fuer externe Broker
CREATE TABLE IF NOT EXISTS positions (
    ticker        TEXT PRIMARY KEY,
    tv_symbol     TEXT,                          -- TradingView-Format (Migration auf Bestandstabellen)
    currency      TEXT,                          -- echte IBKR-Currency (z.B. 'HKD'), aus Positions-Sync
    ibkr_exchange TEXT,                           -- echte IBKR-primaryExchange (z.B. 'SEHK'), aus Positions-Sync
    entry_date    DATE,
    entry_price   NUMERIC,
    qty           NUMERIC,
    stop_price    NUMERIC,
    broker        TEXT NOT NULL DEFAULT 'ibkr',  -- 'ibkr' | 'boom' | 'consors' | 'ing' | 'targo'
    updated       TIMESTAMPTZ DEFAULT NOW()
);

-- ─── disco ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS disco_prices (
    ticker TEXT    NOT NULL,
    date   DATE    NOT NULL,
    open   NUMERIC,
    high   NUMERIC,
    low    NUMERIC,
    close  NUMERIC,
    volume BIGINT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS disco_meta (
    ticker      TEXT PRIMARY KEY,
    last_update DATE
);

-- Computed RRG-Daten nach jedem disco-Run (neu — bisher nur in Excel/HTML)
CREATE TABLE IF NOT EXISTS rrg_data (
    ticker       TEXT    NOT NULL,
    layer        TEXT    NOT NULL,  -- 'asset', 'sector', 'region', 'fx', 'factor'
    week         DATE    NOT NULL,
    quadrant     TEXT,              -- 'Leading', 'Improving', 'Weakening', 'Lagging'
    rs_ratio_pct NUMERIC,          -- Percentrank RS-Ratio (x-Achse RRG, 0-100)
    rs_mom_z     NUMERIC,          -- Z-Score RS-Momentum (y-Achse RRG)
    fast_z       NUMERIC,
    weeks_in_q   INT,
    PRIMARY KEY (ticker, layer, week)
);
CREATE INDEX IF NOT EXISTS rrg_week_idx ON rrg_data(week DESC);

-- Makro-Kontext nach jedem disco-Run (neu — bisher nur in HTML-Banner)
CREATE TABLE IF NOT EXISTS macro_context (
    date        DATE PRIMARY KEY,
    vix         NUMERIC,
    hy_spread   NUMERIC,
    yield_curve NUMERIC,
    dxy         NUMERIC,
    m2_yoy      NUMERIC
);

-- ─── Cluster: Watchlists / PF-Listen / Micro-Listen ─────────────────────────

CREATE TABLE IF NOT EXISTS clusters (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    created TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT clusters_name_unique UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS cluster_views (
    cluster_id  INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    view_name   TEXT    NOT NULL CHECK (view_name = ANY (ARRAY['watchlist', 'micro', 'portfolio'])),
    assigned_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (cluster_id, view_name)
);
CREATE INDEX IF NOT EXISTS cluster_views_view_name_idx ON cluster_views (view_name);

CREATE TABLE IF NOT EXISTS cluster_items (
    cluster_id  INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    tv_symbol   TEXT NOT NULL,
    ticker      TEXT GENERATED ALWAYS AS (split_part(tv_symbol, ':', 2)) STORED,
    added       TIMESTAMPTZ DEFAULT now(),
    klasse      TEXT,
    benchmark   TEXT DEFAULT 'AMEX:SPY',
    notizen     TEXT,
    ibkr_status TEXT DEFAULT 'resolved',
    sec_type    TEXT,
    PRIMARY KEY (cluster_id, tv_symbol)
);
CREATE INDEX IF NOT EXISTS cluster_items_ticker_idx ON cluster_items (ticker);

-- ─── Micro / Fundamentals ────────────────────────────────────────────────────

-- source: 'tv' (TradingView-Scraper), 'ibkr', 'reuters'
-- TV-JSONs bleiben vorläufig als Redundanzkopie
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker         TEXT    NOT NULL,
    updated        DATE    NOT NULL,
    source         TEXT    NOT NULL DEFAULT 'tv',
    exchange       TEXT    NOT NULL DEFAULT '',
    pe             NUMERIC,
    ev_ebitda      NUMERIC,
    roe            NUMERIC,
    debt_equity    NUMERIC,
    roic           NUMERIC,
    revenue_growth NUMERIC,
    ranking_score        NUMERIC,
    ranking_pos          INT,
    score_trends         NUMERIC,
    score_cashflow       NUMERIC,
    score_profitability  NUMERIC,
    score_valuation      NUMERIC,
    score_liquidity      NUMERIC,
    score_solvency       NUMERIC,
    PRIMARY KEY (ticker, updated, source)
);
CREATE INDEX IF NOT EXISTS fundamentals_updated_idx ON fundamentals(updated DESC);

-- Wochenkurse für alle Micro-Ticker (von IBKR reqHistoricalData)
CREATE TABLE IF NOT EXISTS prices (
    ticker   TEXT    NOT NULL,
    exchange TEXT    NOT NULL DEFAULT '',
    date     DATE    NOT NULL,
    open     NUMERIC,
    high     NUMERIC,
    low      NUMERIC,
    close    NUMERIC,
    volume   BIGINT,
    PRIMARY KEY (ticker, exchange, date)
);
CREATE INDEX IF NOT EXISTS prices_ticker_date_idx ON prices (ticker, exchange, date DESC);
