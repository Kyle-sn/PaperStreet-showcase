# Strategy

Design guidelines, conventions, and constraints for strategy development in PaperStreet.

---

## Frequency and Scope

PaperStreet targets **mid-frequency strategies**. This means:

- Signal horizons of **minutes to days** — not tick-by-tick, not monthly rebalancing
- Primary data resolution: **1-minute to 1-day OHLCV bars**
- Acceptable latency for signal computation and order submission is on the order of seconds
- Strategies are not latency-sensitive; a few seconds of slippage between signal and execution
  is acceptable and expected at this frequency

This scope is a deliberate constraint. The infrastructure (Python, IBKR API, SQLite) is well-suited
to mid-frequency and would require significant re-architecture for HFT.

---

## Multi-Strategy Operation

PaperStreet is designed to run multiple single-symbol strategies concurrently. Each strategy instance trades one symbol (existing constraint). Portfolio-level diversification emerges from running several uncorrelated strategies in parallel — not from any single strategy being internally diversified. 

Concurrent strategies must trade disjoint symbol universes. IBKR reports one position per symbol, so two strategies sharing a symbol cannot have their inventory attributed separately, which breaks broker-authoritative position injection (on_bar(bar, position)). Per-strategy position attribution is explicitly out of scope; the universe-disjointness rule is the substitute.

Joint-behavior gate. Before a second (or Nth) strategy is allocated capital alongside the live book, evaluate it jointly against the existing strategies — not just on standalone Sharpe. Compute cross-strategy return correlation, joint drawdown (depth and timing of shared worst periods), and combined Shardpe on overlapping windows. Critically, use tail-conditional correlation (correlation on down-market days / high-VIX subsamples), not full-sample Pearson — long-biased strategies decorrelate in calm markets and converge in selloffs, so full-sample correlation understates the risk that matters. A candidate that is Sharpe-positive standalone but shares its left tail with the existing book adds little diversification and may not warrant allocation. This is a gate on the allocation decision, not a step in the per-strategy 9-step workflow.

Implications:

- Each strategy must be Sharpe-positive on its own. Do not pursue strategies that depend on cross-sectional effects (inverse-vol weighting across instruments, basket-level vol targeting, cross-asset relative value) — those require multi-symbol infrastructure not yet built.
- Strategies are selected for standalone viability. Naturally single-symbol strategies (intraday patterns, mean reversion, calendar effects, single-instrument vol/term-structure trades) fit. Cross-sectional strategies (TSMOM on a basket, pairs, cross-asset RV) do not.
- Account-level constraints apply across all running strategies, not per-strategy — see `RISK.md`.
- Capital allocation between strategies is currently implicit. When more than one strategy reaches paper trading, an explicit allocation rule needs to be committed.

---

## Regime-Switching and Adaptive Strategies (optional family)

This is one strategy family among several, and an **opt-in** one. The default PaperStreet
strategy is unconditional — it runs the same logic in all states. Nothing here changes
that default; this section applies only to a candidate whose thesis is explicitly
regime-conditional.

**Two things called "regime" are not the same:**

- **Adaptive (continuous).** Parameters update smoothly with recent data — e.g.
  vol-targeted position sizing off a trailing realized-vol estimate. This is already in
  use, uncontroversial, and in scope. It does not partition the sample into discrete
  episodes, so the discipline below does not apply to it.
- **Regime-switching (discrete).** The strategy occupies one of a small number of states
  and behaves differently per state — changing signal direction, or turning on/off. This
  is where the statistical danger concentrates, and what the rules below govern.

**Design rules for a regime-switching candidate:**

- **Separate the detector from the in-regime signal.** Build them as two objectives in
  the notebook. The split is for interpretability and prior-pinning — it does **not**
  reduce the search burden, it relocates it.
- **The detector is pinned by an economic/structural prior and committed before looking,
  not fitted.** The total search space is (detector configs) × (signal configs), and the
  detector is the more dangerous half because each configuration *repartitions the data*.
  A two-parameter in-regime signal can still overfit catastrophically through a tuned
  threshold. Prefer a regime variable exogenous to the strategy's own P&L (the
  underlying's trend or realized vol, a published term-structure sign) with a threshold
  set from a structural argument, not a grid search over conditional Sharpe.
- **Keep the in-regime signal to ≤2–3 parameters and the taxonomy to ≤3 states.** The
  binding constraint is the number of independent *episodes*, not trading days: a decade
  of daily data may contain only a handful of distinct risk-off episodes, so a per-regime
  Sharpe rests on N≈few and a five-state model cannot be estimated.
- **The binding benchmark is the *unconditional* strategy, not cash.** A regime filter
  that flattens you in "bad" states is a market-timing overlay — structurally the
  `timing_sma` case already in `BACKTESTING.md`. The regime-gated version must beat the
  same base signal run always-on, net of the extra turnover the gating creates and net of
  the multiple-testing for the gate. Beating cash (or buy-and-hold) while failing to beat
  the unconditional strategy means the gate did nothing.

**Scope.** Single-symbol regime-switching is in scope (no cross-sectional infrastructure
needed). Cross-sectional strategies remain out of scope per Multi-Strategy Operation
above.

---

## Strategy Interface

There are two strategy families, kept deliberately separate because they consume different
inputs and cannot be used interchangeably:

- **Bar strategies** (`strategy/base_strategy.py::BaseStrategy`) — consume OHLCV bars via
  `on_bars` and emit a list of `OrderRequest`s. This is the default family.
- **Quoting strategies** (`strategy/base_quoting_strategy.py::BaseQuotingStrategy`) — consume
  fair-value estimates via `on_estimates` and return a per-symbol mapping of two-sided quote
  dicts (e.g. the ERCOT market maker).

### Symbol universe — single-symbol is N=1

A strategy instance trades a *list* of symbols (`self.symbols`, set by the registry factory).
Single-symbol is the degenerate N=1 case and stays the common shape today. The general entry
points take a per-symbol dict (`on_bars(bars, positions)`, `on_estimates(estimates, positions)`)
keyed by symbol, and single-symbol strategies implement only the convenience method (`on_bar` /
`on_estimate`) that the base class dispatches to when `len(symbols) == 1`. `self.symbol` remains
a single-symbol convenience that returns `symbols[0]` (and raises for a multi-symbol instance).

> This is the **strategy-interface** generalization (`MULTI_STRATEGY_REFACTOR.md` → Phase 1). The
> *operational* "Concurrent strategies must trade disjoint symbol universes" rule above is a
> separate concern and is **unchanged** until it is retired deliberately (that doc's Open
> Decision #5, Phase 4). Generalizing the interface does not by itself relax that rule.

### BaseStrategy (bar family)

```python
class BaseStrategy(SymbolsMixin, ABC):
    name: str                          # unique id; set by @register_strategy; used for DB tagging
    symbols: list[str]                 # set by build_strategy() at construction (self.symbol = N=1)

    # General entry point (engine + live loop call this); default dispatches to on_bar for N=1:
    def on_bars(self, bars: dict[str, dict],
                positions: dict[str, float]) -> list[OrderRequest] | None: ...

    # Single-symbol convenience — override this for the common case:
    def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None: ...

    # Order-construction helpers (auto-tag symbol + strategy name; pass symbol= for multi-symbol):
    def buy(self, quantity, order_type="MKT", limit_price=None, tif="DAY", symbol=None) -> OrderRequest: ...
    def sell(self, quantity, order_type="MKT", limit_price=None, tif="DAY", symbol=None) -> OrderRequest: ...

    # Lifecycle hooks — default to no-ops, override as needed:
    def on_start(self): ...            # load warm-up history here
    def on_stop(self): ...             # clean up here
    def on_fill(self, action, quantity, price): ...
```

`on_bars` returns a **list** of `OrderRequest`s (empty for no action). A single-symbol strategy
implements `on_bar` and returns one `OrderRequest` (or `None`); the base `on_bars` wraps it in a
one-element list. Build orders with `self.buy()` / `self.sell()` so `symbol` and `strategy` are
tagged automatically — a multi-symbol strategy passes `symbol=` to tag the right leg.

### OrderRequest

Strategies never build IBKR `Order` objects directly. They return `OrderRequest` objects
(`strategy/signal.py`, a plain dataclass) that the execution layer translates: `Portfolio`
in backtest, `orders/order_types.py::order_from_request` → `placeOrder` in live.

```python
@dataclass
class OrderRequest:
    action:      str        # 'BUY' or 'SELL'
    quantity:    float
    order_type:  str = 'MKT'   # 'MKT' or 'LMT'
    limit_price: float | None = None
    tif:         str = 'DAY'
    symbol:      str = ''   # populated automatically by buy()/sell()
    strategy:    str = ''   # populated automatically from strategy.name
```

The field is `action` (not `side`) to match `orders/order_types.py` and `Portfolio`, so it
threads through to IBKR without remapping. `__post_init__` validates action, order_type,
limit_price presence, and positive quantity.

---

## Data Flow Into a Strategy

Strategies receive data through `on_bars()` callbacks (one bar per symbol, keyed by symbol;
single-symbol strategies see a one-key dict and usually read it via the `on_bar` convenience).
Each bar dict has the same shape as what comes out of `IBApp.historicalData` (see `DATA_MODEL.md`):

```python
{
    "datetime":  str,
    "open":      float,
    "high":      float,
    "low":       float,
    "close":     float,
    "volume":    Decimal,
    "wap":       float,
    "bar_count": int,
}
```

Strategies maintain their own internal bar history (e.g. a `deque` or pandas `DataFrame`) and
compute signals from it. They should not reach into `IBApp` directly for market data.

---

## Position Awareness

**Inventory awareness is a requirement, not an option — every PaperStreet strategy is
inventory-aware.** Signals are gated on the current position (e.g. `position < max_position`
before adding, `position > 0` before selling) so the strategy never double-enters or oversizes
an exit. This is a deliberate, system-wide design constraint with two consequences worth stating:

- Strategies are **path-dependent**: the decision on bar N depends on fills realized over bars
  1…N-1. This is why backtesting uses the custom event-loop engine rather than a vectorized
  library (see `BACKTESTING.md` → Architecture, and `ROADMAP.md` → Decided Against for the full
  rationale).
- Position must come from an authoritative source, injected per call (below), so the strategy's
  view of inventory matches reality in both live and backtest.

A strategy should know its current position to avoid double-entry and to size exit orders
correctly. There are two approaches:

1. **Track internally**: maintain a `self._position` counter updated by `on_fill()`. Simple,
   but can drift if fills come from outside the strategy (e.g. manual trades, liquidations).
2. **Query IBApp**: call `ib_app.get_position(symbol)` as the authoritative source. Slightly
   more latency, but always reflects broker-confirmed state.

Recommended (and what the shipped strategies do): do not self-track. Position is injected into
`on_bar(bar, position)` by the caller — `session.get_position(symbol)` live, `Portfolio.position`
in backtest. This eliminates drift when a signal is rejected downstream.

---

## Risk Controls

Risk is enforced at two layers (strategy layer, then a system-wide orders layer). The full model
and the division of responsibility live in `RISK.md` → Where Risk Checks Belong. A strategy author
is responsible only for the **strategy layer**:

- Per-strategy position cap: never emit an `OrderRequest` that pushes inventory past the strategy's
  `max_position`.
- Flat-at-close (if applicable): exit positions before market close for day-trade strategies.
- More generally: do not emit `OrderRequest`s that violate the strategy's own rules.

System-wide hard limits that no strategy can bypass (per-order size cap, kill switch) live in the
orders layer, not the strategy — see `RISK.md` → Where Risk Checks Belong.

---

## Registry and Selection

Strategies are selected **by name**, not by import. Each concrete strategy registers itself
with a decorator:

```python
@register_strategy("spy_short_reversal")    # bar family
class SpyShortReversalStrategy(BaseStrategy): ...

@register_quoting_strategy("ercot_market_making")   # quoting family
class ERCOTMarketMakingStrategy(BaseQuotingStrategy): ...
```

`strategy/__init__.py` imports every concrete module so the registries are populated on
`import strategy`. Entry points then build by name + config — no imports to edit when swapping:

```python
from strategy.registry import build_strategy
s = build_strategy("spy_short_reversal", symbol="SPY")
```

`run_live.py` drives off a `STRATEGY_NAME` / `STRATEGY_PARAMS` config block at the top of the
file; `backtesting/run_backtest.py` drives off a `CONFIG = BacktestConfig(strategy_name=...,
strategy_params=...)` block (see `BACKTESTING.md`). Both select the strategy by name — no import
edits when swapping.

## Naming and File Conventions

- Each strategy lives in its own file under `strategy/in_progress/`, `strategy/parked/`, or `strategy/` (for utilities like benchmarks)
- File name matches strategy class name in snake_case
- Strategy `name` must be unique across all strategies — it is the registry key and tags
  orders/executions in the database. The `@register_*` decorator sets `cls.name` for you.

---

## State and Warm-Up

Mid-frequency strategies typically require a lookback period before generating valid signals
(e.g. a 20-period moving average needs 20 bars of history).

Use `strategy/indicators.py::RollingWindow` for bounded rolling state — it is a `deque` with
fixed `maxlen`, so memory stays constant in a long-running live loop (a plain list grows
without bound). `RollingWindow.ready` is `True` once the window is full; gate signals on it:

```python
self.prices.append(bar["close"])
if not self.prices.ready:
    return None        # warm-up
```

For a faster start, pre-load the lookback in `on_start()` (preferred: from the `bars` table;
otherwise `reqHistoricalData`). Never generate signals from an under-populated window — a common
source of spurious trades at system start.

---

## What This Is Not

- **Not a signal research environment**: use `research/` notebooks for that
- **Not a backtester**: strategies should be backtested in `backtesting/` before being wired
  into the live system (see `BACKTESTING.md`)
- **Not responsible for order routing**: strategies emit `OrderRequest`s and do not call
  `placeOrder` directly

---

## Strategy Ideas / Research Status

_(Update this section as research progresses.)_

| Strategy | Status | Notes |
|---|---|---|
| `spy_short_reversal` | **Parked at §7 OOS** | RSI(2)<10 + SMA(200) filter, exit close>SMA(5), single-entry long-only. OOS Sharpe 0.52 failed to beat timing_sma (0.63) or buy_and_hold (0.70). Code reusable; candidate parked. See `research/research_notes/short_reversal_strategy_notes.md`. |
| `buy_and_hold`, `timing_sma` | Benchmarks | Evaluation baselines in `strategy/benchmarks.py` (not trading candidates) — run through the same engine for apples-to-apples comparison. `timing_sma` (long while close>SMA(n)) is the *binding* benchmark for long-only timing-overlay strategies. |
| _regime candidate (none active)_ | Family note | Optional branch — see "Regime-Switching and Adaptive Strategies". Single-symbol discrete state-switching is in scope; validate with the methods toolbox in `BACKTESTING.md`, benchmark against the *unconditional* strategy. |

Signals under consideration:
- Momentum / trend following on daily bars
- Mean reversion on intraday bars (1-min, 5-min)
- Volatility-based position sizing
- Gap fade (overnight gap continuation or reversal)

Signals not being pursued:
- Tick-level microstructure (outside mid-frequency scope)
- Options strategies (infrastructure not built out)
