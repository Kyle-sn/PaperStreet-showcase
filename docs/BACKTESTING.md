# Backtesting

Design notes and conventions for PaperStreet's backtesting harness.

---

## Purpose

The `backtesting/` module provides a way to evaluate strategy logic against historical bar data
without connecting to TWS or submitting real orders. A strategy that passes backtest validation
is a prerequisite for running it in paper trading.

Backtesting in PaperStreet is intentionally simple — no order book simulation, no intraday
tick replay, no multi-asset portfolio optimization. The goal is fast iteration on signal quality
and basic parameter validation at mid-frequency bar resolution.

---

## Frequency and Data

Strategies target **minutes-to-days** hold times, so the primary testing resolution is:

- **1-minute bars** for intraday strategies
- **Daily bars** for multi-day / swing strategies

Bar data comes from the local `bars` database table (see `DATA_MODEL.md`). If data is not
available locally, it can be fetched from IBKR via `reqHistoricalData` and stored before
running a backtest.

IBKR historical data availability (approximate):
- 1-min bars: up to 30 days back
- 5-min bars: up to 6 months back
- Daily bars: up to 20 years back (for most liquid instruments)

For deeper history, alternative sources (Yahoo Finance via `yfinance`, Polygon.io, etc.) can
supplement IBKR data. Store everything in the same `bars` table schema for uniformity.

---

## Architecture

The backtesting harness is a lightweight event loop that replays bars in chronological order
and calls the same strategy interface used in live trading. A strategy should not know whether
it is running live or in a backtest.

It is deliberately an **event loop**, not a vectorized engine. Every PaperStreet strategy is
inventory-aware (see `STRATEGY.md` → Position Awareness): signals are gated on the position
realized by prior fills, so the strategy is path-dependent. A bar-by-bar loop that feeds
`portfolio.position` back into `on_bar` is the natural fit. Vectorized backtesters are not used —
see `ROADMAP.md` → Decided Against for the full rationale. When research needs parameter sweeps,
loop this engine rather than reaching for one of those libraries.

A run is fully described by a single `BacktestConfig` (`backtesting/config.py`) — strategy
name + params, symbol, data window, and cost/fill model. `run_backtest(config)`
(`backtesting/runner.py`) is the one-call entry point; it builds the strategy by name from the
same registry the live loop uses, so swapping anything is a config change, not an engine edit.

```
  BacktestConfig  (strategy name + params, symbol, window, costs, fill model)
        │
        ▼
  BarDataSource (backtesting/data.py)        cache-first: market_data_bars → IBKR on miss
        │  DataFrame: datetime + OHLCV
        ▼
  BacktestEngine (backtesting/engine.py)
        │  per bar: fill prior signal → strategy.on_bar(bar, portfolio.position) → queue
        ▼
  SimBroker (backtesting/broker.py)          fills + commission + slippage → Fill
        │
        ▼
  Portfolio (backtesting/portfolio.py)       long/short cash + position + realized PnL
        │
        ▼
  compute_metrics → BacktestResult           equity curve + trades + metrics; .summary()
```

Module map:

| File | Responsibility |
|---|---|
| `config.py` | `BacktestConfig` — the declarative run spec |
| `data.py` | `load_bars()` — cache-first loader (`db` / `ibkr` / `auto`) |
| `broker.py` | `SimBroker` — fill model, commission, slippage; emits `Fill` |
| `portfolio.py` | `Portfolio` — cash/position accounting with long & short support |
| `engine.py` | `BacktestEngine` — the replay loop (enforces no-lookahead) |
| `metrics.py` | `compute_metrics()` — the output-metrics table below |
| `result.py` | `BacktestResult` — equity + trades + metrics, `.summary()` / frames |
| `runner.py` | `run_backtest(config)` — orchestration entry point |
| `run_backtest.py` | thin CLI with a `CONFIG` block, like `run_live.py` |

### SimBroker fill assumptions

`SimBroker` handles `OrderRequest`s with simple, conservative assumptions:

- **Market orders**: fill at the next bar's open (`fill="next_open"`, the default and only
  lookahead-safe model), then pay `slippage_bps` of that price — buys fill higher, sells lower.
- **Limit orders**: fill only if the bar trades through the limit (buy: low ≤ limit; sell:
  high ≥ limit), at the better of the limit and the reference price. Limit fills pay no slippage.
- Commission is `max(commission_min, commission_per_share × qty)` per fill.
- Good-for-one-bar: an unfilled limit is dropped, not carried forward. No partial fills, no
  queue position.

These are optimistic assumptions. Real fill quality at mid-frequency will be worse, especially
in illiquid names or around data releases. A `fill="close"` model (fill on the signalling bar's
own close) exists for quick comparisons only — it is optimistic and not lookahead-safe.

---

## Transaction Costs

Always include transaction costs. IBKR charges approximately:
- **Equities**: $0.005/share, minimum $1.00, maximum 1% of trade value (tiered pricing)
- **No exchange fees** for most retail orders (payment for order flow / PFOF model varies)

Apply a simple per-share commission in the backtest engine rather than ignoring costs. For
mid-frequency strategies with many small trades, commissions can materially erode returns.

Slippage modeling:
- A conservative default: assume you pay **half the average bid-ask spread** per side
- For liquid large-caps (SPY, AAPL, etc.), spread is negligible at mid-frequency sizes
- For small/mid-caps, spread can be a significant cost

---

## Avoiding Lookahead Bias

The most common backtesting error. Rules:

1. A bar signal is computed **after the bar closes** — you cannot use the close price of bar N
   to trade at the open of bar N. You can only trade at the open of bar N+1 or later.
2. Never use future bar data (N+1, N+2, ...) to compute a signal for bar N.
3. When using pandas, avoid `shift()` mistakes — be explicit about which period's data is
   used for signal generation vs. which period's price is used for fill simulation.
4. Volume-weighted features (VWAP, WAP) from bar N are only known after bar N closes.

The backtest engine enforces rule 1 by design: under the default `next_open` model, each bar
first fills the order queued on the *previous* bar (at this bar's open) and only then calls
`strategy.on_bar` on this bar's close, so a signal can never trade on the same bar that produced
it. Rules 2-4 are the responsibility of the strategy author.

---

## Output Metrics

A backtest run should produce at minimum:

| Metric | Description |
|---|---|
| Total return | Cumulative % return over the period |
| Annualized return | Geometric annualized return |
| Sharpe ratio | Risk-adjusted return (annualized, excess over cash — see cash convention below) |
| Max drawdown | Peak-to-trough decline as % of portfolio value |
| Win rate | % of trades that were profitable |
| Avg win / avg loss | Ratio of average winning trade to average losing trade |
| Total trades | Number of round-trip trades |
| Total commission | Total commissions paid |

### Cash convention (idle cash and the Sharpe risk-free rate)

Two related, separable choices:

- **How idle cash is credited in the equity curve.** `Portfolio.mark` holds uninvested cash at
  **0%** — when a strategy is flat its equity is flat. This is the current default and is what the
  metrics are computed on.
- **The risk-free rate in the Sharpe numerator.** `compute_metrics(..., risk_free_rate=r)` (annual,
  default `0.0`) subtracts a per-bar `r / periods_per_year` from each bar return ("excess over
  cash"). Default `0.0` keeps the historical behavior, which is *self-consistent* with idle cash at
  0% (the realized cash return is 0%, so excess-over-cash = raw return).

These two must agree to be meaningful: a genuine excess-over-cash Sharpe credits idle cash at the
same rate it subtracts as `risk_free_rate`. When they agree, **the Sharpe ratio is nearly invariant
to the cash convention** — only *absolute* return moves (materially so for a strategy that sits in
cash most of the time). So the cash convention is the dominant lever for annualized return, **not**
for Sharpe. Worked both-ways comparison: `research/killed/spy_short_reversal/sensitivity.py` (§5 Task 0 of
the SPY short-reversal notes).

---

## Benchmarking

**Benchmark-attribution principle.** The binding benchmark is always the candidate with the one
component whose contribution is in question removed — beating buy-and-hold but not *that*
benchmark means the removed component did the work, not the signal under test.

For a long-only strategy that pulls to cash below a trend filter, that's `timing_sma` (entry signal
removed, timing kept): does the signal beat a pure trend timer? For a regime-gated strategy it's the
unconditional strategy (gate removed, signal kept): does conditioning beat always-on? Commit the
comparison metric (Sharpe, drawdown) **before** looking.

A strategy's metrics mean nothing in isolation — they must beat doing something simpler.
`strategy/benchmarks.py` provides two baselines that run through the **same** engine, broker,
and cost model (so the comparison is apples-to-apples, not a hand-rolled equity curve):

- **`buy_and_hold`** — always invested. Usually the highest raw return (it compounds and never
  sits in cash); the case against it is risk-adjusted (drawdown, Sharpe).
- **`timing_sma`** — long only while `close > SMA(n)`, cash otherwise, with **no** reversion
  signal. For any long-only strategy that pulls to cash below a trend filter, this is the
  **binding** benchmark — attribute the edge to the signal by subtracting the timing-only baseline.

Worked example: `research/killed/spy_short_reversal/is_backtest.py` runs the three-way
(candidate / buy-and-hold / timing-only) comparison on one ADJUSTED_LAST basis.

## Data basis (`what_to_show`)

`BacktestConfig.what_to_show` selects the data series. `"TRADES"` (split-adjusted only, price
return) is the project default; `"ADJUSTED_LAST"` (split *and* dividend adjusted, total-return) is
opt-in per strategy. For SPY the TRADES/ADJUSTED_LAST ratio runs ~1.45→1.00 over 1996→2026 —
price-return drops the entire dividend stream.

`what_to_show` is a first-class, configurable parameter (not a hardcoded constant): it threads
through the IBKR request and cache upsert, `MarketDataService.get_*`, `BacktestConfig.what_to_show`,
and the `run_live.py` fetch. It is also part of the `market_data_bars` cache key, so a TRADES
series and an ADJUSTED_LAST series for the same symbol/bar_size coexist without colliding.

**Keep research, backtest, and live on one basis per strategy.** Return-based signals (RSI, SMA)
barely move between bases, but the equity curve and any total-return benchmark are off by the whole
dividend stream if research/backtest/live don't agree — live fills at raw price plus
dividends-as-cash sum to the same total return the adjusted backtest shows. A deviation from this
(e.g. `overnight_drift`'s TRADES-plus-explicit-dividend-overlay basis) must be justified and
documented at the basis's configuration site.

See `IBKR_NOTES.md` for the IBKR API parameter itself.

## Validation methods (select by strategy structure)

A backtest is validated with one of the methods below. **The default is the single
IS/OOS split**, and it is the right choice for most PaperStreet strategies — do not reach
for the heavier machinery unless a trigger applies. The heavier methods are matched to
strategy classes that need them; they are not a correctness patch the default was missing.

- **Single IS/OOS split + one-shot OOS — default.** Develop on the first portion, hold out
  the tail, run the locked candidate once. Sound for path-dependent, inventory-aware
  event-loop strategies trading on realized signals bar-by-bar: the engine already enforces
  no-lookahead via the `next_open` fill, so adjacent-bar leakage is not a concern. Use this
  unless a trigger below applies.

- **Purged + embargoed cross-validation — use only when labels overlap.** When a strategy
  constructs *overlapping forward-return windows* (a notebook signal-research pattern more
  than an event-loop one), naive splits leak across the boundary regardless of regime. Purge
  the boundary observations whose label windows straddle a split and embargo a buffer after
  each test block. A plain bar-by-bar realized-signal strategy does **not** need this.

- **Walk-forward / CPCV — use for regime/adaptive or low-episode candidates.** Replaces the
  *single IS backtest* with multiple train/test recombinations, and lives **entirely inside
  the IS window** — it does not touch the held-out OOS. Report metrics **per episode**, not
  pooled: a fold that "passes" while containing zero instances of a given regime is
  uninformative about that regime, so always show which folds contained each state. CPCV
  gives more recombinations than anchored walk-forward but cannot manufacture episodes that
  do not exist; with N≈few episodes the conditional estimates carry wide intervals — say so.

- **Deflated Sharpe / multiple-testing correction — apply whenever selection occurred.**
  When a candidate was chosen after searching more than one configuration, deflate the
  selected Sharpe for the number of trials. **The trial count must include the detector
  search** (5 regime definitions × 6 signal lookbacks = 30 trials, not 6). This is an
  IS-selection correction applied *before* the OOS is spent; it complements the one-shot
  OOS, it does not replace it. When a genuinely pre-committed single configuration was run
  (N=1), the deflated Sharpe equals the raw Sharpe — it is a no-op, so applying it always
  costs nothing.

All of the above operate within the IS window. **The terminal OOS one-shot stays a single
held-out shot** under every method.

> **Harness note.** The current event-loop engine does not implement walk-forward / purged
> CV out of the box — textbook sklearn-style CPCV assumes a vectorized feature/label matrix
> and does not map onto a path-dependent event loop. Doing this here means block-wise
> event-loop replay over time partitions, purging the boundary bars whose signal/label
> windows straddle a split, plus per-regime metric aggregation. That is a real build, gated
> on a regime candidate actually existing — do not build it speculatively.

### Free parameters: pre-declared selection vs post-hoc re-tuning

"Parameter sensitivity, NOT optimization" (used throughout the `RESEARCH_WORKFLOW_*.md` docs)
collapses two different rules. Split apart:

- **(a) PROHIBITED — re-tuning after seeing a result.** Adjusting a parameter, especially
  after a gate fails, is the anti-pivot rule: the fixed historical sample has already been
  queried, and re-querying it is unaccounted multiple comparisons. This is what the
  Parameter Sensitivity step in every existing workflow doc guards against, and it still
  applies in full.
- **(b) PERMITTED — selecting a parameter that was never pinned a priori.** A candidate can
  have a genuinely free parameter with no canonical or structurally-defensible value (unlike
  `spy_short_reversal`, which adopted published Connors values in Step 3 and so never had one
  to select). Selecting such a parameter is not optimization **provided the search is
  declared before any data is touched and the trial count is carried** — it is a one-time
  specification choice, not a re-tuning loop.

**Requirements for (b):**

- The free parameter is named in the candidate's **Step 3 (Signal)** framing, not introduced
  at Step 5. See the copy-in block below.
- The grid is pre-committed and **coarse**. Adjacent cells at spacing finer than the
  parameter's economic resolution are not independent tests — they inflate the trial count
  for no information.
- The **selection rule** is pre-committed in writing (e.g. plateau center, or median cell of
  the grid) **before the surface is viewed**. Choosing the selection rule after seeing the
  surface reintroduces exactly the bias the pre-commitment exists to prevent.
- The **full grid is reported** in the workflow doc, not the max cell — follow the 4x4 table
  format already used in `short_reversal_strategy_notes.md` §5 (the `rsi_entry ×
  sma_trend` grid).
- **Trial count N for the deflated Sharpe includes every axis searched across the whole
  candidate**, not just the final grid — see "Deflated Sharpe / multiple-testing correction"
  above.
- A pre-committed **total IS trial budget**. Default: 30-50 for a single-symbol daily
  candidate, set per candidate, not a hard rule.
- **Step 5 then confirms the selected point sits on a plateau.** It still does not re-pick
  the spec — the same plateau-not-spike test applies to a pre-declared free parameter as to
  any other.

All of this lives inside the IS window; the terminal OOS one-shot is unchanged — consistent
with every validation method above operating within IS.

Risk-control parameters (stops, caps, vol targets) are a related but separate case — see
`docs/RISK.md` → "Tail-Control Parameter Selection: Drawdown Budget, Not Sharpe." They are
selected against a drawdown budget, not this Sharpe-plateau rule.

#### Free-parameter declaration block

No standard (non-regime) `RESEARCH_WORKFLOW` template file exists yet to host this natively —
the only copy-in template in the repo, `RESEARCH_WORKFLOW_regime_template.md`, is an explicit
regime-only addendum. Until a standard template exists, copy the block below directly into a
candidate's own Step 3 (Signal); it follows the same blockquoted, angle-bracket-placeholder
pattern as the `[REGIME BRANCH]` blocks in that file.

Copy this block into Step 3 when the candidate has a parameter with no a-priori value.
**A candidate with zero free parameters omits this block entirely** — and adopting a
published or canonical value (as `spy_short_reversal` did) is the preferred path whenever
one exists; only use this block when no such value is available.

> **[FREE PARAMETER]** `<parameter name>` — no a-priori value because `<why no canonical or
> structural value exists>`.
>
> - **Grid (pre-committed, coarse):** `<axis name: v1, v2, v3, ...>`
> - **Selection objective and rule:** `<metric, e.g. IS Sharpe> / <rule, e.g. plateau center>`,
>   fixed before the surface is viewed.
> - **Trial count:** `<N>` cells this axis; running candidate total after this axis: `<N>`.
> - **Fill/cost sweep:** `<is a fill-model or cost assumption swept as an additional axis? If
>   yes, name it and say why; if no, say so explicitly.>`

---

## What Backtesting Does Not Validate

- **Execution risk**: slippage, partial fills, order rejections
- **Connectivity risk**: what happens if TWS disconnects mid-trade
- **IBKR-specific behavior**: paper fills ≠ real fills ≠ backtest fills
- **Regime change**: a strategy that worked 2019-2022 may not work in a different
  volatility regime. For a regime-conditional candidate, validate with walk-forward / CPCV
  and per-episode reporting (see Validation methods above), not a single pooled backtest.
- **Overfitting**: running many parameter combinations on the same dataset produces
  spurious results. When a candidate was selected after a search, report a deflated Sharpe
  with the full trial count (including any detector search) before spending the OOS.

Run any strategy on **out-of-sample data** before paper trading. Split your dataset: use the first
portion for development and the held-out tail for final validation.

Portfolio-level evaluation across multiple strategies is not currently built. Each strategy is backtested in isolation. When multiple strategies are candidates for concurrent live operation, combining their equity curves and computing portfolio-level metrics (combined Sharpe, joint drawdown, cross-strategy correlation) becomes necessary — see ROADMAP.md backlog. Computing those metrics as post-processing on independent backtest results is much lighter work than a full multi-symbol backtester, but it assumes the strategies do not compete for capital — and at the $50k default base capital (`BacktestConfig.starting_cash`, `backtesting/config.py`) that assumption is exactly where it breaks, not a free pass. On a small account, two or three concurrent strategies' notionals are a large fraction of equity, so capital *is* likely binding: independent post-processing then overstates what the book can actually hold simultaneously, and the gap between it and the truth is wide precisely at deployable capital. The faithful version is a shared-cash replay: run all strategies against a single Portfolio with one cash account, so the combined equity curve respects the real budget constraint. Pure post-processing is an upper bound on achievable combined performance, not an estimate of it — it is only a safe approximation on the large-capital research track, where per-strategy notionals are small relative to equity and capital does not bind for a handful of strategies.

---

## Usage

Programmatic (notebooks, scripts):

```python
from backtesting import BacktestConfig, run_backtest

result = run_backtest(BacktestConfig(
    strategy_name="spy_short_reversal", symbol="SPY",
    bar_size="1 day", starting_cash=50_000, slippage_bps=1.0,
    what_to_show="ADJUSTED_LAST",
))
print(result.summary())          # metrics table
result.equity_frame().plot()     # equity curve
result.trades_frame()            # trade log
```

CLI: edit the `CONFIG` block in `backtesting/run_backtest.py`, then
`python backtesting/run_backtest.py`.

Data is read from the local `market_data_bars` cache by default (`data_source="auto"`) and
only fetched from TWS on a miss. Pre-warm a symbol for fully offline iteration with
`python -m backtesting.data SYMBOL [bar_size] [duration]`.

## Status

The **bar-strategy** harness is built (`backtesting/`, see the module map above) with hermetic
tests in `tests/test_backtest.py`. A separate **quoting-family** (event-replay) backtester for
`BaseQuotingStrategy` is not yet built (see `ROADMAP.md`).
