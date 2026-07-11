"""
Hermetic tests for the SPY short-reversal candidate and its indicator.

Covers:
  - WilderRSI reproduces the research notebook's pandas RSI(2) exactly (the
    live/backtest signal-parity guarantee).
  - The frozen Option A rule (entry RSI<10 & close>SMA200; exit close>SMA5;
    single entry) matches an independent vectorized implementation.
  - Single-entry discipline (no averaging down) and full-position exit.
  - Registry wiring for the candidate and the two benchmarks.
"""

import math

import pandas as pd
import pytest

from strategy.indicators import WilderRSI
from strategy.registry import STRATEGY_REGISTRY, build_strategy
from strategy.parked.spy_short_reversal import SpyShortReversalStrategy


def _pandas_rsi(closes, period):
    s = pd.Series(closes)
    delta = s.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    ru = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rd = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - 100 / (1 + ru / rd)


# A deterministic path: a rising drift with periodic sharp 2-bar dips, so the
# RSI(2)<threshold + above-trend entry actually fires and later exits.
def _sawtooth(n=140):
    closes = []
    price = 100.0
    for i in range(n):
        price *= 1.01  # steady uptrend keeps price above a lagging SMA20
        if i % 15 in (13, 14):
            price *= 0.96  # two-bar shock -> RSI(2) collapses below 10
        closes.append(round(price, 2))
    return closes


# ---------------------------------------------------------------------------
# WilderRSI
# ---------------------------------------------------------------------------

def test_rsi_matches_pandas_reference():
    closes = _sawtooth()
    ref = _pandas_rsi(closes, 2)
    ind = WilderRSI(2)
    for i, c in enumerate(closes):
        ind.update(c)
        if math.isnan(ref.iloc[i]):
            assert ind.value is None
        else:
            assert ind.value == pytest.approx(ref.iloc[i], abs=1e-9)


def test_rsi_warmup_and_all_gains():
    ind = WilderRSI(2)
    ind.update(100)            # no diff yet
    assert ind.value is None and not ind.ready
    ind.update(101)            # one diff -> still warming (min_periods=2)
    assert ind.value is None
    ind.update(102)            # two diffs, all gains -> RSI saturates at 100
    assert ind.ready and ind.value == 100.0


# ---------------------------------------------------------------------------
# Strategy rule equivalence
# ---------------------------------------------------------------------------

def _reference_actions(closes, rsi_entry, sma_exit, sma_trend, rsi_period, target_notional):
    """Independent vectorized implementation of the frozen rule, run as the same
    immediate-fill state machine the driver below uses."""
    s = pd.Series(closes)
    rsi = _pandas_rsi(closes, rsi_period)
    sma_e = s.rolling(sma_exit).mean()
    sma_t = s.rolling(sma_trend).mean()
    pos, out = 0, []
    for i, c in enumerate(closes):
        ready = not (math.isnan(rsi.iloc[i]) or math.isnan(sma_e.iloc[i]) or math.isnan(sma_t.iloc[i]))
        action = None
        if ready:
            if pos > 0:
                if c > sma_e.iloc[i]:
                    action = ("SELL", pos)
            elif rsi.iloc[i] < rsi_entry and c > sma_t.iloc[i]:
                qty = int(target_notional // c)
                if qty > 0:
                    action = ("BUY", qty)
        out.append(action)
        if action:
            pos = pos + action[1] if action[0] == "BUY" else 0
    return out


def _drive(strategy, closes):
    """Feed closes through on_bar, injecting position as a fill-immediately state
    machine — isolates the strategy's signal logic from the engine's next-open
    mechanics (those are covered in test_backtest.py)."""
    pos, out = 0, []
    for i, c in enumerate(closes):
        bar = {"datetime": i, "open": c, "high": c, "low": c, "close": c, "volume": 1}
        sig = strategy.on_bar(bar, position=pos)
        out.append(None if sig is None else (sig.action, sig.quantity))
        if sig is not None:
            pos = pos + sig.quantity if sig.action == "BUY" else 0
    return out


def test_strategy_matches_reference_rule():
    closes = _sawtooth()
    params = dict(rsi_entry=10.0, sma_exit=5, sma_trend=20, rsi_period=2, target_notional=50_000.0)
    strat = SpyShortReversalStrategy(**params)
    got = _drive(strat, closes)
    expected = _reference_actions(closes, **params)
    assert got == expected
    # The path must actually exercise both sides, or the equivalence is vacuous.
    assert any(a and a[0] == "BUY" for a in got)
    assert any(a and a[0] == "SELL" for a in got)


def test_single_entry_no_averaging_down():
    # While already long, a fresh oversold bar must NOT add to the position.
    strat = SpyShortReversalStrategy(rsi_entry=10.0, sma_exit=5, sma_trend=20)
    closes = _sawtooth()
    saw_long_oversold_bar = False
    pos = 0
    for i, c in enumerate(closes):
        bar = {"datetime": i, "open": c, "high": c, "low": c, "close": c, "volume": 1}
        # Force a held long and an oversold reading by replaying when long.
        sig = strat.on_bar(bar, position=10 if pos == 0 else pos)
        if pos > 0 and strat.rsi.ready and strat.rsi.value is not None and strat.rsi.value < 10:
            # Long + oversold: the only allowed action is a SELL exit, never a BUY.
            assert sig is None or sig.action == "SELL"
            saw_long_oversold_bar = True
        pos = 10  # keep it long for the rest of the scan
    assert saw_long_oversold_bar  # the scenario was actually hit


def test_exit_sells_full_injected_position():
    strat = SpyShortReversalStrategy(sma_exit=2, sma_trend=3)
    # Prime indicators with a few bars, held long.
    for i, c in enumerate([100, 101, 102, 103]):
        bar = {"datetime": i, "open": c, "high": c, "low": c, "close": c, "volume": 1}
        sig = strat.on_bar(bar, position=37)  # arbitrary broker-confirmed long
    # Final bar is above SMA(2) -> exit, selling exactly the injected position.
    assert sig is not None and sig.action == "SELL" and sig.quantity == 37


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_wiring():
    import strategy  # noqa: F401  (populates the registry)
    for name in ("spy_short_reversal", "buy_and_hold", "timing_sma"):
        assert name in STRATEGY_REGISTRY
    built = build_strategy("spy_short_reversal", symbol="SPY",
                           params={"rsi_entry": 10.0})
    assert built.symbol == "SPY" and built.name == "spy_short_reversal"
