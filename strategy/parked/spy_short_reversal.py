"""
spy_short_reversal.py

SPY short-horizon mean-reversion candidate (the Connors/Alvarez RSI(2) system).

This is NET-NEW code. It does not subclass or reuse MeanReversionStrategy: that
class trades a (close - SMA)/population-std deviation with no trend filter, which
is a different signal. This strategy is RSI-based with a long-term trend gate.

Research provenance
-------------------
Implements "Option A" frozen in research/killed/research_notes/short_reversal_strategy_notes.md
after §3 signal characterization (gate = GO, in-sample 1996-2014, ADJUSTED_LAST
total-return basis). The RSI(2) here reproduces the notebook's signal exactly
(strategy.indicators.WilderRSI is validated bit-for-bit against the notebook's
pandas computation), so live and backtest emit the same signals the research
measured.

FROZEN SPEC (do not re-tune; §5 parameter sensitivity is a separate step)
-------------------------------------------------------------------------
  Entry : RSI(2) < 10  AND  close > SMA(200)        [oversold inside an uptrend]
  Exit  : close > SMA(5)                            [single rule — the canonical
          RSI(2) short-MA-cross; NOT a compound exit]
  Entry style : single entry — enter only when flat, never scale in (Decision #9).
  Trend filter : 200-day SMA, mandatory (sidesteps buying into regime changes).

Economic rationale: equity indices overreact to short-term shocks and partially
revert over the following days; we harvest that reversion long-only and only
while the index is above its 200-day average, standing aside in downtrends where
"dips" are the front of a regime change (the strategy's core catastrophic risk).

Position sourcing (Position Rule)
---------------------------------
This strategy does NOT track its own position. Net position is injected via the
`position` arg of on_bar (broker-confirmed live; Portfolio.position in backtest),
so a downstream-rejected signal can never desync internal inventory. Entries gate
on `position <= 0` (flat) and the exit sells exactly `position` to go flat.

Sizing
------
Single-entry, fixed-notional: buy floor(target_notional / close) shares. For a
$50k account on SPY this is a near-fully-invested single position that amortizes
the IBKR $1 commission minimum to a fraction of a bp (a 10-share order would pay
the $1 floor and bleed ~10bp/side instead). target_notional is a fixed dollar
target, not current equity, so position size does not compound across trades.

STATUS: PARKED at §7 OOS (2026-06-14)
--------------------------------------
Failed the binding OOS gate: 2015+ Sharpe 0.52 does not beat timing_sma (0.63)
or buy_and_hold (0.70). The reversion entry adds nothing OOS; it's a 200-day
timer with extra steps. One-shot honored — no re-tune.
See research/killed/research_notes/short_reversal_strategy_notes.md §7.
"""

from __future__ import annotations

from strategy.base_strategy import BaseStrategy
from strategy.indicators import RollingWindow, WilderRSI
from strategy.registry import register_strategy
from strategy.signal import OrderRequest
from utils.log_config import setup_logger

logger = setup_logger(__name__)


@register_strategy("spy_short_reversal")
class SpyShortReversalStrategy(BaseStrategy):
    """
    RSI(2) mean-reversion with a 200-day trend filter (frozen Option A).

    Parameters
    ----------
    rsi_period : int
        RSI lookback. Default 2 (the canonical RSI(2)).
    rsi_entry : float
        Enter when RSI is strictly below this. Default 10.0.
    sma_exit : int
        Exit when close rises above this SMA. Default 5.
    sma_trend : int
        Trend-filter SMA length; entries require close above it. Default 200.
    target_notional : float
        Dollar notional per entry; shares = floor(target_notional / close).
        Default 50_000.0 (sized to the research $50k account).
    """

    name = "spy_short_reversal"

    def __init__(
        self,
        rsi_period: int = 2,
        rsi_entry: float = 10.0,
        sma_exit: int = 5,
        sma_trend: int = 200,
        target_notional: float = 50_000.0,
    ):
        self.rsi_entry = rsi_entry
        self.target_notional = target_notional

        self.rsi = WilderRSI(rsi_period)
        self.exit_sma = RollingWindow(sma_exit)
        self.trend_sma = RollingWindow(sma_trend)

    def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None:
        close = bar["close"]
        self.rsi.update(close)
        self.exit_sma.append(close)
        self.trend_sma.append(close)

        # Warm-up: no signal until every indicator is primed (the 200-day SMA
        # dominates, so this stands aside for the first ~200 bars).
        if not (self.rsi.ready and self.exit_sma.ready and self.trend_sma.ready):
            return None

        # Holding a long: the only action is the bounce exit. No averaging down,
        # no re-entry while in the trade (single-entry rule).
        if position > 0:
            if close > self.exit_sma.mean():
                logger.info(
                    f"date={bar['datetime']}|EXIT close={close:.4f}>SMA{self.exit_sma.size}"
                    f"={self.exit_sma.mean():.4f}|position={position}"
                )
                return self.sell(position)
            return None

        # Flat: enter on an oversold day inside the uptrend.
        if position <= 0 and self.rsi.value < self.rsi_entry and close > self.trend_sma.mean():
            qty = int(self.target_notional // close)
            if qty <= 0:
                return None
            logger.info(
                f"date={bar['datetime']}|ENTRY rsi2={self.rsi.value:.2f}<{self.rsi_entry}|"
                f"close={close:.4f}>SMA{self.trend_sma.size}={self.trend_sma.mean():.4f}|"
                f"qty={qty}|position={position}"
            )
            return self.buy(qty)

        return None
