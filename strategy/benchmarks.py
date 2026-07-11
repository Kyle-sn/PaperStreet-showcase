"""
benchmarks.py

Evaluation baselines that run through the *same* engine, broker, and cost model
as a candidate strategy, so a comparison is apples-to-apples (same fills, same
slippage/commission, same data basis) rather than a hand-rolled equity curve.

A long-only timing-overlay strategy must justify itself against doing something
simpler. These provide the two mandatory benchmarks (docs/BACKTESTING.md, and
the SPY short-reversal notes §4):

  - buy_and_hold : always invested — the raw-return / risk-adjusted floor.
  - timing_sma   : long only while close > SMA(n), cash otherwise, with NO
                   reversion signal. This is the *binding* bar: a candidate that
                   pulls to cash below the trend filter is a market-timing
                   overlay with an entry rule bolted on, so beating buy-and-hold
                   but not timing-only means the timing did the work, not the
                   signal.

Both size fixed-notional (floor(target_notional / close) shares) to match the
candidate's sizing, and both are inventory-aware via the injected `position`
(Position Rule) — no internal position tracking.
"""

from __future__ import annotations

from strategy.base_strategy import BaseStrategy
from strategy.indicators import RollingWindow
from strategy.signal import OrderRequest
from strategy.registry import register_strategy


@register_strategy("buy_and_hold")
class BuyAndHoldStrategy(BaseStrategy):
    """Buy once on the first bar and hold for the whole window.

    Parameters
    ----------
    target_notional : float
        Dollar notional of the single entry; shares = floor(notional / close).
    """

    name = "buy_and_hold"

    def __init__(self, target_notional: float = 50_000.0):
        self.target_notional = target_notional
        self._seen = 0

    def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None:
        # Honor the framework warm-up contract (no signal off a single bar); the
        # one-bar entry delay is immaterial over a multi-year window.
        self._seen += 1
        if self._seen < 2:
            return None
        # Enter once, from flat; never add. Inventory-aware so a rejected order
        # is simply retried next bar rather than double-counted.
        if position > 0:
            return None
        qty = int(self.target_notional // bar["close"])
        return self.buy(qty) if qty > 0 else None


@register_strategy("timing_sma")
class TimingSmaStrategy(BaseStrategy):
    """Long while close > SMA(n), flat otherwise. No reversion signal.

    Parameters
    ----------
    sma_trend : int
        Trend-filter SMA length. Default 200.
    target_notional : float
        Dollar notional per entry; shares = floor(notional / close).
    """

    name = "timing_sma"

    def __init__(self, sma_trend: int = 200, target_notional: float = 50_000.0):
        self.target_notional = target_notional
        self.trend_sma = RollingWindow(sma_trend)

    def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None:
        close = bar["close"]
        self.trend_sma.append(close)
        if not self.trend_sma.ready:
            return None

        above = close > self.trend_sma.mean()
        if above and position <= 0:
            qty = int(self.target_notional // close)
            return self.buy(qty) if qty > 0 else None
        if not above and position > 0:
            return self.sell(position)
        return None
