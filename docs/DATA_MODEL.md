# Data Model

Data structures, database schema, and storage conventions for PaperStreet.

---

## Storage Backend

PaperStreet uses **SQLite** for local persistence. The database file lives at a path defined in
the `env` configuration. SQLite is sufficient for a single-machine, single-user system at
mid-frequency scale — query volume is low and the dataset fits comfortably in a single file.

All database access is centralized in the `database/` module:
- `database/account.py` — account and position snapshots
- `database/trading.py` — orders, executions, and trade groups

No other module should issue raw SQL. If new persistence is needed, add a function to the
appropriate submodule.

### SQLite → Postgres migration trigger (pre-committed)

The trigger to migrate off SQLite is **more than one process writing concurrently**, not query
volume or dataset size. This is already anticipated: e.g. a separate data-pull script writing
`market_data_bars` while the live app writes `executions`/`orders` — two processes, two writers,
one file. SQLite serializes writers with a database-level lock, so the failure mode is
**single-writer lock contention** surfacing as `database is locked` (`SQLITE_BUSY`) errors under
concurrent writes, not corruption. WAL mode (already enabled) allows concurrent *readers* alongside
one writer but does **not** grant concurrent *writers*.

This is **not a migration to do now** — it is a pre-committed trigger so the decision is already
made when it fires. Multi-process operation is expected (the live app, data-pull scripts, and
research sessions all open the same DB). When sustained concurrent writing becomes routine — or
`database is locked` errors start appearing in logs — migrate to Postgres. Until then, SQLite is
sufficient for the single-writer-at-a-time reality.

---

## In-Memory State (IBApp)

These dicts live on the `IBApp` instance and represent the current broker-confirmed state.
They are the source of truth during a live session. They are **not** persisted as-is — snapshots
are written to the DB on every update callback.

### `self.account`

```python
{
    "cash_balance":        float | None,   # TotalCashBalance (USD)
    "net_liquidation":     float | None,   # NetLiquidation (USD)
    "gross_position_value": float | None,  # GrossPositionValue (USD)
    "buying_power":        float | None,   # BuyingPower (USD)
    "excess_liquidity":    float | None,   # ExcessLiquidity (USD)
    "maintenance_margin":  float | None,   # MaintMarginReq (USD)
    "initial_margin":      float | None,   # InitMarginReq (USD)
    "realized_pnl":        float | None,   # RealizedPnL (USD)
    "unrealized_pnl":      float | None,   # UnrealizedPnL (USD)
}
```

Updated by `updateAccountValue` callbacks. Protected by `_account_lock`.
Access via `get_current_cash_balance()`, `get_realized_pnl()`, etc.

### `self.positions`

```python
{
    "AAPL": {
        "position":      float,   # Net shares held. Negative = short.
        "market_price":  float,   # Last known market price from IBKR
        "market_value":  float,   # position * market_price
        "average_cost":  float,   # Cost basis per share (includes commissions)
        "unrealized_pnl": float,
        "realized_pnl":  float,
    },
    # ... one entry per symbol with non-zero position
}
```

Updated by `updatePortfolio` callbacks. Symbols with `position == 0` are removed.
Protected by `_account_lock`. `get_position(symbol)` is the access path for position state; it is
also what feeds the `position` argument injected into strategies — see `STRATEGY.md` → Position
Awareness for that rule.

Note: keyed by `contract.symbol` (e.g. `"AAPL"`), not by `conId`. For most equity strategies
this is unambiguous. If the system ever trades instruments where the same symbol appears on
multiple exchanges or in multiple currencies, this key scheme will need revision.

---

## Database Tables

### `account_snapshots`

Point-in-time snapshots of the account summary, written on every `accountDownloadEnd` callback.

```sql
CREATE TABLE account_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    account             TEXT    NOT NULL,
    captured_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cash_balance        REAL,
    net_liquidation     REAL,
    gross_position_value REAL,
    buying_power        REAL,
    excess_liquidity    REAL,
    maintenance_margin  REAL,
    initial_margin      REAL,
    realized_pnl        REAL,
    unrealized_pnl      REAL
);
```

### `position_snapshots`

Point-in-time snapshots of individual positions, written on every `updatePortfolio` callback.
This creates a time series of position state — useful for PnL attribution and debugging.

```sql
CREATE TABLE position_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account         TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    captured_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    position        REAL    NOT NULL,
    sec_type        TEXT,
    currency        TEXT,
    con_id          INTEGER,
    market_price    REAL,
    market_value    REAL,
    average_cost    REAL,
    unrealized_pnl  REAL,
    realized_pnl    REAL
);
```

### `orders`

One row per order submitted to IBKR. Written at order submission time; updated as `orderStatus`
callbacks arrive.

```sql
CREATE TABLE orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ib_order_id         INTEGER UNIQUE NOT NULL,   -- client-assigned orderId
    ib_perm_id          INTEGER,                   -- IBKR permanent ID (set after submission)
    symbol              TEXT    NOT NULL,
    sec_type            TEXT,
    side                TEXT    NOT NULL,           -- 'BUY' or 'SELL'
    order_type          TEXT    NOT NULL,           -- 'MKT', 'LMT', etc.
    quantity            REAL    NOT NULL,
    limit_price         REAL,
    status              TEXT,                       -- last known IBKR status string
    filled_quantity     REAL,
    remaining_quantity  REAL,
    avg_fill_price      REAL,
    last_fill_price     REAL,
    why_held            TEXT,
    strategy            TEXT,                       -- strategy name that originated the order
    trade_group_id      TEXT,                       -- logical trade this order belongs to (trades.trade_group_id); nullable
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

`trade_group_id` groups the N order rows of one logical trade. A single-symbol round
trip has two orders (entry + exit) sharing one id; a basket later shares one id across
all legs. It is **nullable** and applied **going forward only** — historical orders are
not backfilled (see `MULTI_STRATEGY_REFACTOR.md` Open Decision #1). The write path stamps
it at submission (see `trades` below).

### `executions`

One row per execution (partial or full fill). Linked to `orders` via `order_id`. Written by
`execDetails` callback; commission fields updated by `commissionAndFeesReport`.

```sql
CREATE TABLE executions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id            INTEGER REFERENCES orders(id),
    ib_exec_id          TEXT    UNIQUE NOT NULL,   -- IBKR execution ID
    ib_order_id         INTEGER NOT NULL,
    symbol              TEXT    NOT NULL,
    sec_type            TEXT,
    account             TEXT,
    side                TEXT    NOT NULL,           -- 'BOT' or 'SLD' (IBKR convention)
    shares              REAL    NOT NULL,
    price               REAL    NOT NULL,
    avg_price           REAL,
    cum_qty             REAL,
    executed_at         DATETIME NOT NULL,
    exchange            TEXT,
    liquidation         BOOLEAN NOT NULL DEFAULT FALSE,
    commission          REAL,
    commission_currency TEXT,
    realized_pnl        REAL,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### `trades`

One row per **logical trade** — the grouping concept that ties together the N order rows
(`orders.trade_group_id`) belonging to one position. A single-symbol round trip is one
`trades` row (its entry and exit orders share the `trade_group_id`); a basket later shares
one row across all legs. Written by `database/trading.py::assign_trade_group` at order
submission.

```sql
CREATE TABLE trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_group_id TEXT NOT NULL UNIQUE,        -- UUID shared by every order in the trade
    strategy_name  TEXT,
    symbols        TEXT NOT NULL,               -- single symbol now; comma-separated basket later
    side           TEXT,                        -- entry action: 'BUY' (long) or 'SELL' (short)
    quantity       REAL,                        -- entry quantity
    status         TEXT NOT NULL DEFAULT 'open' -- 'open' | 'closed' | 'partial' | 'broken'
                   CHECK(status IN ('open', 'closed', 'partial', 'broken')),
    opened_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at      TEXT
);
CREATE INDEX idx_trades_open ON trades(strategy_name, symbols, status);
```

**The table is the open-group registry.** On each order submission the write path
(`assign_trade_group`) looks up the open row for `(strategy_name, symbols)`:
- **no open row** → the order opens a new logical trade: a fresh UUID is minted and an
  `open` row is inserted (recording the entry `side`).
- **an open row exists** → the order joins it (the matching exit, or a same-direction
  scale-in) and reuses its `trade_group_id`.

**Close is fill-driven, not submission-driven.** A trade stays `open` until executions
confirm the group is flat: `save_execution` reconciles the group's net signed filled
shares (BOT positive, SLD negative) and transitions `open → closed` when they return to 0.
A partial exit leaves a non-zero net and keeps the trade `open`.

`partial` and `broken` are reserved for future multi-leg atomicity handling (a basket entry
with a rejected leg — `MULTI_STRATEGY_REFACTOR.md` Open Decision #4); nothing emits them yet.

### `market_data_bars`

Historical and live bar data stored by `market_data/`. Enables backtesting without re-fetching
from IBKR and provides a local history for signal computation on reconnect. `what_to_show` is
part of the unique key (TRADES vs ADJUSTED_LAST series coexist) — see `CONVENTIONS.md` → Data
basis for the convention.

```sql
CREATE TABLE market_data_bars (
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
```

#### Write-path semantics

`database/market_data.py::upsert_bars` is the **single write boundary** for bar data. It
uses `INSERT ... ON CONFLICT DO UPDATE`, so a re-pull of the same window **overwrites** all
OHLCV/wap/bar_count fields on existing rows rather than silently retaining stale data. This
means any bar corruption (bad price, wrong volume) is self-healing on the next fetch of the
affected window — no manual intervention required.

For targeted force-refresh of a known-bad date range, `delete_bars_window(symbol, start, end,
...)` deletes all cached rows in `[start, end]` inclusive, scoped by symbol/bar_size/what_to_show.
After deletion, re-fetch from IBKR to repopulate.

`validate_bars(symbol, fresh_bars, ...)` compares cached bars against a reference pull and
returns per-field discrepancies (bar_datetime, field name, cached value, fresh value). Use
`python -m database.validate_cache SYMBOL WHAT_TO_SHOW [--fix]` to run validation against a
live IBKR pull.

All incoming `bar_datetime` values are normalized to canonical ISO 8601 at write time
(`_normalize_bar_datetime`) so the UNIQUE key dedups correctly regardless of caller format.

---

## Key Data Structures (Python)

### Bar (in-memory, from historicalData callback)

Currently stored as a plain dict in `self.historical_data`. The shape is:

```python
{
    "datetime":  str,    # IBKR date string — format varies by bar size
                         # e.g. "20240115 09:30:00 US/Eastern" for intraday
                         #      "20240115" for daily
    "open":      float,
    "high":      float,
    "low":       float,
    "close":     float,
    "volume":    Decimal,
    "wap":       float,   # bar.average — volume-weighted avg price
    "bar_count": int,     # number of trades in the bar
}
```

When consuming bar data for strategy work, convert `datetime` to a proper `datetime` object.
Be mindful of timezone — IBKR returns intraday times in the exchange timezone unless overridden.

### Contract (IBKR)

Use IBKR's `Contract` object from `ibapi.contract`. Minimum fields for a US equity:

```python
from ibapi.contract import Contract

contract = Contract()
contract.symbol   = "AAPL"
contract.secType  = "STK"
contract.currency = "USD"
contract.exchange = "SMART"   # IBKR smart routing
```

For futures or other instruments, additional fields (`lastTradeDateOrContractMonth`, `multiplier`,
`exchange`) must be set correctly. Define all contracts in `contracts/` — never inline them.

### Order (IBKR)

Use IBKR's `Order` object from `ibapi.order`. Minimum fields for a market order:

```python
from ibapi.order import Order

order = Order()
order.action        = "BUY"    # or "SELL"
order.orderType     = "MKT"
order.totalQuantity = 100
order.tif           = "DAY"    # time-in-force: DAY, GTC, IOC, etc.
```

Order construction should live in `orders/` only.

---

## Conventions

- All monetary values are stored as `REAL` (float) in USD unless otherwise noted.
- `bar_datetime` in `market_data_bars` is always stored as a canonical **ISO 8601** string —
  date-only (`"YYYY-MM-DD"`) for daily/weekly/monthly bars, full ISO for intraday.
  `database/market_data.py::_normalize_bar_datetime` coerces every write (raw IBKR
  `"YYYYMMDD"` / `"YYYYMMDD  HH:MM:SS"` or already-ISO) into this form so the UNIQUE key
  dedups correctly. `migrate_bar_datetimes()` (run by `initialize_db`) rewrites any legacy
  non-ISO rows and drops the duplicates they created.
- `executed_at` in `executions` uses the timestamp string from IBKR's `Execution.time` field,
  which is in the format `"YYYYMMDD  HH:MM:SS"` (note double space). Parse before storing if
  you want proper DATETIME indexing.
- IBKR uses `1e308` as a sentinel for "not available" on float fields in `CommissionAndFeesReport`.
  This is already handled in `commissionAndFeesReport()` — store `None` when the value is `>= 1e308`.
- `side` in `executions` follows IBKR convention: `"BOT"` (bought) and `"SLD"` (sold).
  In `orders`, use `"BUY"` and `"SELL"` to match IBKR's `Order.action` field.

  ---

  ## Continuous-contract construction (diversified-trend futures)

Two series per instrument; NOT interchangeable.

1. Signal series — ratio (proportional) back-adjusted. At each roll, scale prior
   history by (new_price / old_price) on the roll date so % returns are continuous
   across the splice. Signals (sign of trailing 6–12m return) use this series only.
   Ratio-adjusted returns are anchor-invariant — pct_change is unchanged by which
   contract anchors the series, so the series can be built on IS-only now and
   extended to OOS later without revising any IS signal value. Never additive/Panama
   adjust (distorts back-history % returns; can go negative — cf. WTI 2020).

2. PnL series — actual contracts, explicit rolls. Hold the designated contract at
   real prices. On a roll, close the expiring leg and open the next at their real
   settles, booking the actual calendar spread (roll yield = real PnL) plus two legs
   of commission + slippage. Roll yield appears here once, via the actual roll — do
   NOT also apply adjusted-series returns to PnL (double-count).

Roll trigger (Stage 0): deterministic calendar.
- Financials (ES, 6E, 6J, 6A): roll ~5 business days before last-trade-date.
- Deliverable commodities (CL, ZN, GC, HG): roll a few days before first-notice-date so
  delivery is never risked.
Deterministic ⇒ point-in-time, reproducible, no OI/volume dependence. OI-crossover
is a Stage-1 refinement, not built now.

Execution: trades fill next-session open (Fri signal → Mon open); rolls book at
roll-date settle (mismatch immaterial at multi-week horizon). Apply identically
across sources; parity-check if IBKR + a vendor are mixed.

## Other (unsure the best section for this to live in)
Evaluation is regime-segmented, NOT pooled. Do not report a single full-sample
Sharpe as the headline.
- Base case (conservative): 2011–2019 trend drought. If the strategy can't survive
  this, it's not viable regardless of full-sample numbers.
- Stress checks: 2020 (COVID) and 2022 (inflation/rates) — confirm crisis-convexity
  behavior shows up in-sample.
- Anchor expected Sharpe / DD priors on the drought decade, not the pooled average.
- Inspect WHERE PnL originates (e.g. is rates PnL a non-repeatable secular-bull
  artifact?), not just how much.

Databento pulls persisted as raw .dbn.zst (immutable source-of-truth, re-derivable
without re-billing) → parsed via the official client (.to_df(), correct fixed-point
price + ns-timestamp handling) → SQLite market_data cache (research layer). CSV/JSON
only as derived artifacts, never the primary store.

## Futures Research Database

Separate SQLite at `data/futures_research.db` (research layer only, not
production). Built by `python -m research.killed.diversified_trend.build_continuous`
from raw Databento .dbn.zst files. Schema:

### `futures_contracts`
Contract metadata: raw_symbol, root, expiration, delivery_month, multiplier,
tick_size. One row per unique (raw_symbol, expiration) — same raw_symbol can
appear in different decades (CME single-digit year codes cycle every 10 years).

### `futures_settlements`
Daily settlement prices: (raw_symbol, trade_date, settlement). Source:
Databento statistics schema, stat_type=3 (SETTLEMENT_PRICE), last record per
symbol per date (most final/official value).

### `futures_roll_calendar`
Roll events: (root, roll_date, from_symbol, to_symbol). Deterministic calendar
rolls: cash-settled/FX (ES, 6E, 6J, 6A) roll 5 bdays before expiry;
deliverables (ZN, GC, HG) roll 3 bdays before first-notice; CL rolls 5 bdays
before expiry (expiry is already well before delivery month).

### `futures_continuous`
Two continuous series per instrument per date: signal_price (ratio-adjusted,
for computing trend signals) and pnl_daily_usd (actual settle-to-settle dollar
PnL per contract, for portfolio accounting). Columns: root, trade_date,
active_symbol, raw_settle, signal_price, pnl_daily_usd, is_roll, sample
(WARMUP / IS / OOS).
