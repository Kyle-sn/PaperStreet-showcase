# strategy/

This directory contains all trading strategies. Each strategy inherits from `BaseStrategy`. A single-symbol strategy implements `on_bar`; the multi-symbol entry point is `on_bars` (the base class dispatches `on_bars` → `on_bar` when the instance trades one symbol). See `docs/STRATEGY.md` and `docs/MULTI_STRATEGY_REFACTOR.md`.

---

## Adding a New Strategy

### 1. Create your strategy file in the appropriate subdirectory

- `strategy/` (top level) — complete, stable strategies (e.g. `benchmarks.py`)
- `strategy/in_progress/` — strategies under active development (kept in the private working repo; not included in this mirror)
- `strategy/parked/` — strategies that were researched and set aside

Inherit from `BaseStrategy`, add the `@register_strategy` decorator, and implement `on_bar`. That is the only contract required.

```python
# strategy/my_strategy.py

from strategy.base_strategy import BaseStrategy
from strategy.registry import register_strategy
from strategy.signal import OrderRequest
from utils.log_config import setup_logger

logger = setup_logger(__name__)


@register_strategy("my_strategy")
class MyStrategy(BaseStrategy):

    def __init__(self, ...):
        # Store parameters
        # Initialize any internal indicator state (e.g. price history)
        # Do NOT initialize a position counter here — see Position Rule below
        ...

    def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None:
        # Process the incoming bar
        # Return self.buy(...) / self.sell(...), or None
        ...
```

### 2. Register it in `strategy/__init__.py`

Add one import line so the decorator runs on package import:

```python
from strategy import my_strategy  # noqa: F401
```

### 3. Select it via config

In `run_live.py`, set the name and params at the top of the file:

```python
STRATEGY_NAME = "my_strategy"
STRATEGY_PARAMS = {"window": 20, ...}
```

For a backtest, edit the `CONFIG` block in `backtesting/run_backtest.py` (or call
`run_backtest(BacktestConfig(...))` directly):

```python
CONFIG = BacktestConfig(strategy_name="my_strategy", symbol="SPY",
                        strategy_params={"window": 20, ...})
```

Both call `build_strategy(name, symbols=[...], params=...)` (the legacy `symbol="SPY"` kwarg is still accepted as a single-symbol alias) — no import edits needed when swapping strategies.

---

## The `on_bar` Interface

A single-symbol strategy implements this signature (a multi-symbol strategy overrides
`on_bars(bars, positions) -> list[OrderRequest]` instead — see `docs/STRATEGY.md`):

```python
def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None:
```

**`bar`** is a dict with the following keys:

| Key        | Type    | Description              |
|------------|---------|--------------------------|
| `datetime` | str     | Timestamp of the bar     |
| `open`     | float   | Open price               |
| `high`     | float   | High price               |
| `low`      | float   | Low price                |
| `close`    | float   | Close price              |
| `volume`   | Decimal | Volume                   |

**`position`** is the current net shares held. See the Position Rule below.

**Return value** is either `None` (no action) or an `OrderRequest` built with `self.buy()` / `self.sell()`:

```python
return self.buy(quantity=10)                               # market order
return self.sell(quantity=10, order_type="LMT", limit_price=155.00)  # limit order
```

`self.buy()` / `self.sell()` auto-populate `symbol` and `strategy` on the `OrderRequest` — do not set them by hand.

---

## Position Rule

**Strategies must not track their own position internally.**

Position is always injected via the `position` parameter of `on_bar`:

- **In live trading** — the caller passes `IBApp.get_position(symbol)`, which is populated by the `updatePortfolio` EWrapper callback from TWS. This is the broker-confirmed position.
- **In backtesting** — the engine passes `Portfolio.position`, which is updated after each processed signal.

Self-tracking causes drift — the strategy's internal count diverges from reality if the portfolio layer rejects a signal or a fill is partial. See `docs/STRATEGY.md` → Position Awareness for the full rule and rationale.

---

## Existing Strategies

**Top-level**

| File                       | Class                 | Family  | Description                              |
|----------------------------|-----------------------|---------|------------------------------------------|
| `base_strategy.py`         | `BaseStrategy`        | —       | Abstract base class for bar strategies.  |
| `base_quoting_strategy.py` | `BaseQuotingStrategy` | —       | Abstract base class for quoting strategies. |
| `benchmarks.py`            | `BuyAndHoldStrategy`  | bar     | Always-invested baseline.                |
|                            | `TimingSmaStrategy`   | bar     | Long while close > SMA(n); the binding timing benchmark. |

**in_progress/**

*(kept in the private working repo; not included in this mirror)*

**parked/**

| File                              | Class                        | Family  | Description                                                               |
|-----------------------------------|------------------------------|---------|----------------------------------------------------------------------------|
| `spy_short_reversal.py`           | `SpyShortReversalStrategy`   | bar     | RSI(2)<10 + SMA(200) filter. Parked at §7: OOS Sharpe 0.52 < benchmarks.  |

---

## Research Workflow

Candidates are researched and validated *before* a strategy class is written, following the staged
workflow in `research/killed/research_notes/RESEARCH_WORKFLOW_regime_template.md`: hypothesis and
kill criteria committed up front, in-sample EDA, a backtest through the custom engine, a
pre-declared parameter-sensitivity check, and a single one-shot out-of-sample test. The rules for
avoiding lookahead and overfitting are in `docs/BACKTESTING.md`. Every candidate that has gone
through it so far is under `research/killed/`, with its workflow doc in `research/killed/research_notes/`.

Only once a candidate clears its out-of-sample gate does it get implemented as a strategy class
here and wired into `run_live.py` / `run_backtest.py` as described above.
