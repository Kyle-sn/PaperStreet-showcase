-- ============================================================
-- PaperStreet Trading Database Schema
-- ============================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ------------------------------------------------------------
-- Market Data Bars (Historical OHLCV)
-- Keyed by symbol+sec_type+bar_size+datetime+what_to_show.
-- Write path uses ON CONFLICT DO UPDATE so re-fetching the
-- same range overwrites stale data (not INSERT OR IGNORE).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_data_bars (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    sec_type     TEXT NOT NULL DEFAULT 'STK',
    bar_size     TEXT NOT NULL,
    bar_datetime TEXT NOT NULL,
    open         REAL NOT NULL,
    high         REAL NOT NULL,
    low          REAL NOT NULL,
    close        REAL NOT NULL,
    volume       REAL,
    wap          REAL,
    bar_count    INTEGER,
    what_to_show TEXT NOT NULL DEFAULT 'TRADES',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(symbol, sec_type, bar_size, bar_datetime, what_to_show)
);

CREATE INDEX IF NOT EXISTS idx_market_data_lookup
    ON market_data_bars(symbol, sec_type, bar_size, bar_datetime);

-- ------------------------------------------------------------
-- Orders (submissions to IBKR)
-- ib_order_id is assigned by IB; id is our local PK.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ib_order_id        INTEGER,
    ib_perm_id         INTEGER,
    ib_parent_id       INTEGER,
    symbol             TEXT NOT NULL,
    sec_type           TEXT NOT NULL DEFAULT 'STK',
    action             TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
    order_type         TEXT NOT NULL,
    tif                TEXT NOT NULL DEFAULT 'DAY',
    quantity           REAL NOT NULL,
    limit_price        REAL,
    stop_price         REAL,
    trail_percent      REAL,
    trail_amount       REAL,
    outside_rth        INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'PENDING',
    filled_quantity    REAL NOT NULL DEFAULT 0,
    remaining_quantity REAL,
    avg_fill_price     REAL,
    last_fill_price    REAL,
    why_held           TEXT,
    strategy_name      TEXT,
    -- Nullable link to the logical trade (trades.trade_group_id) this order belongs
    -- to. One logical trade = N order rows sharing a trade_group_id. Going-forward
    -- only: historical orders are not backfilled (see MULTI_STRATEGY_REFACTOR.md
    -- Open Decision #1). Kept nullable here; tightened in a later phase.
    trade_group_id     TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_orders_ib_order_id  ON orders(ib_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_symbol       ON orders(symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_trade_group  ON orders(trade_group_id);

-- ------------------------------------------------------------
-- Trades (logical trades / trade groups)
-- One row per logical trade. A single-symbol round trip is one trades row
-- (entry order + exit order share its trade_group_id); a basket later shares one
-- row across all legs. The trades table is the open-group registry: the write
-- path looks up the open row for (strategy_name, symbols) to decide whether a new
-- order opens a new group or joins the open one. status transitions open->closed
-- when executions confirm the group is flat (net filled shares return to 0).
-- 'partial'/'broken' are reserved for future multi-leg atomicity handling
-- (Open Decision #4) — nothing emits them yet.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_group_id TEXT NOT NULL UNIQUE,        -- UUID shared by every order in the trade
    strategy_name  TEXT,
    symbols        TEXT NOT NULL,               -- single symbol now; comma-separated basket later
    side           TEXT CHECK(side IN ('BUY', 'SELL')),  -- entry action (long vs short)
    quantity       REAL,                        -- entry quantity
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK(status IN ('open', 'closed', 'partial', 'broken')),
    opened_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_open
    ON trades(strategy_name, symbols, status);

-- ------------------------------------------------------------
-- Executions (fills returned by execDetails callback)
-- ib_exec_id is globally unique per fill from IB.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS executions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id            INTEGER REFERENCES orders(id),
    ib_exec_id          TEXT UNIQUE,
    ib_order_id         INTEGER,
    account             TEXT,
    symbol              TEXT NOT NULL,
    sec_type            TEXT NOT NULL DEFAULT 'STK',
    side                TEXT NOT NULL CHECK(side IN ('BOT', 'SLD')),
    shares              REAL NOT NULL,
    price               REAL NOT NULL,
    cum_qty             REAL,
    avg_price           REAL,
    commission          REAL,
    commission_currency TEXT,
    realized_pnl        REAL,
    liquidation         INTEGER NOT NULL DEFAULT 0,
    exchange            TEXT,
    executed_at         TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_executions_symbol ON executions(symbol, executed_at);

-- ------------------------------------------------------------
-- Position Snapshots
-- Periodic snapshots from updatePortfolio() callbacks.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS position_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account        TEXT NOT NULL,
    con_id         INTEGER,
    symbol         TEXT NOT NULL,
    sec_type       TEXT NOT NULL DEFAULT 'STK',
    currency       TEXT NOT NULL DEFAULT 'USD',
    position       REAL NOT NULL,
    market_price   REAL,
    market_value   REAL,
    average_cost   REAL,
    unrealized_pnl REAL,
    realized_pnl   REAL,
    snapshot_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_position_snapshots
    ON position_snapshots(account, symbol, snapshot_at);

-- ------------------------------------------------------------
-- Account Snapshots
-- Periodic snapshots from updateAccountValue() callbacks.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_snapshots (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    account              TEXT NOT NULL,
    cash_balance         REAL,
    net_liquidation      REAL,
    gross_position_value REAL,
    buying_power         REAL,
    excess_liquidity     REAL,
    maintenance_margin   REAL,
    initial_margin       REAL,
    realized_pnl         REAL,
    unrealized_pnl       REAL,
    snapshot_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_account_snapshots
    ON account_snapshots(account, snapshot_at);

-- ------------------------------------------------------------
-- Strategy Signals
-- Every signal (including None / no-op) emitted by a strategy.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    action        TEXT CHECK(action IN ('BUY', 'SELL')),
    quantity      INTEGER,
    bar_datetime  TEXT,
    bar_close     REAL,
    executed      INTEGER NOT NULL DEFAULT 0,
    order_id      INTEGER REFERENCES orders(id),
    signal_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_strategy_signals
    ON strategy_signals(strategy_name, symbol, signal_at);

