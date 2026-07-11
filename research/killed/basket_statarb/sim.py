"""
sim.py

Research-tier daily spread simulation. A per-basket state machine that replays
the daily spread and emits round-trip trades with after-cost PnL.

NOT THE VALIDATION GATE. This is research/ tooling for eyeballing whether a
candidate spread is tradeable and roughly how it behaves — consistent with
docs/ROADMAP.md -> Decided Against (no second backtester). The PaperStreet
engine (backtesting/) remains the real validation gate at Stage 1. Keep that
distinction: numbers out of here inform candidate selection; they do not certify
a basket.

State machine
-------------
    flat ──(z <= -z_enter)──► long-spread   (buy the spread; expect it to rise)
    flat ──(z >= +z_enter)──► short-spread  (sell the spread; expect it to fall)
    in-position ──► flat  when any of:
        - reversion: |z| <= z_exit
        - z-stop:    z beyond ±z_stop in the adverse direction
        - time-stop: bars_held >= time_stop_mult * half_life

Lookahead & gap conservatism
----------------------------
Decisions are made on bar t's close (z[t]); every fill is at bar t+1's *open*
(same next_open rule as the PaperStreet engine, docs/BACKTESTING.md). Stops are
gap-conservative: a stop triggered at t's close fills at t+1's open even if the
session gapped clean through the stop level — the sim eats the gap the way live
trading will, rather than filling at the stop price.

Sizing
------
Fixed share quantities, frozen for the whole sim (no rebalancing). A single
share scale is chosen so the basket's gross notional at the first tradeable bar
equals `gross_notional`; per-leg shares are `scale * weight_i`, constant
thereafter. Long-spread holds +shares, short-spread holds -shares.

Time bases
----------
The time-stop is in trading bars (half-life is measured in trading time, so
overnight gaps don't pose as fast reversion). Borrow and dividend accruals use
calendar days between fills (financing accrues over weekends). See costs.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from research.killed.basket_statarb import spread as spread_mod
from research.killed.basket_statarb.costs import CostModel


@dataclass
class Trade:
    """One completed round trip."""
    direction: int          # +1 long-spread, -1 short-spread
    entry_date: object
    exit_date: object
    entry_spread: float     # spread value at the entry fill (opens @ weights)
    exit_spread: float      # spread value at the exit fill
    bars_held: int          # trading-day bars between fills
    calendar_days: int      # calendar days between fills (for financing)
    gross_pnl: float        # direction * scale * (exit_spread - entry_spread)
    entry_cost: float
    exit_cost: float
    holding_cost: float     # net financing: borrow − signed dividend overlay
    net_pnl: float
    exit_reason: str        # 'reversion' | 'z_stop' | 'time_stop' | 'forced_eod'


@dataclass
class SimResult:
    """Outcome of a sim run over one basket."""
    trades: list[Trade] = field(default_factory=list)
    half_life: float = float("nan")
    time_stop_bars: float = float("nan")
    share_scale: float = float("nan")
    n_bars: int = 0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def net_pnl(self) -> float:
        return float(sum(t.net_pnl for t in self.trades))

    @property
    def gross_pnl(self) -> float:
        return float(sum(t.gross_pnl for t in self.trades))

    @property
    def total_costs(self) -> float:
        return float(sum(t.entry_cost + t.exit_cost + t.holding_cost for t in self.trades))

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return float("nan")
        wins = sum(1 for t in self.trades if t.net_pnl > 0)
        return wins / len(self.trades)

    @property
    def avg_bars_held(self) -> float:
        if not self.trades:
            return float("nan")
        return float(np.mean([t.bars_held for t in self.trades]))

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


def _spread_at(price_row: pd.Series, weights: np.ndarray) -> float:
    return float(np.dot(price_row.to_numpy(dtype=float), weights))


def simulate(
    closes: pd.DataFrame,
    opens: pd.DataFrame,
    weights: np.ndarray,
    *,
    z_enter: float = 2.0,
    z_exit: float = 0.5,
    z_stop: float = 3.5,
    time_stop_mult: float = 3.0,
    lookback: int = 60,
    gross_notional: float = 10_000.0,
    cost_model: CostModel | None = None,
    dividend_yields: np.ndarray | None = None,
) -> SimResult:
    """Replay the daily spread and return completed round trips.

    Parameters
    ----------
    closes, opens : DataFrame
        Aligned raw-price (ADJUSTED_LAST) panels, same columns/index, oldest
        first. `closes` drives signals; `opens` is where fills land (t+1 open).
    weights : array
        Per-share weight vector aligned to the column order. Fixed shares, no
        rebalancing.
    z_enter, z_exit, z_stop : float
        Entry / reversion-exit / stop thresholds on the rolling z-score.
    time_stop_mult : float
        Time stop = time_stop_mult * half_life (bars). Disabled if half-life is
        not finite.
    lookback : int
        Rolling z-score window (bars).
    gross_notional : float
        Target basket gross notional at the first tradeable bar; sets the fixed
        share scale.
    cost_model : CostModel
        Cost parameters; defaults to CostModel() (ADJUSTED_LAST conventions).
    dividend_yields : array | None
        Per-leg annual dividend yield *fractions* aligned to `closes.columns`.
        Pass these only on the TRADES basis (overlay ON: long legs receive, short
        legs pay); leave None on ADJUSTED_LAST (overlay OFF — dividends already in
        the price path; see costs.py Open Decision #5).
    """
    if list(closes.columns) != list(opens.columns):
        raise ValueError("closes and opens must have identical column order")
    if not closes.index.equals(opens.index):
        raise ValueError("closes and opens must share the same index")
    weights = np.asarray(weights, dtype=float).ravel()
    if weights.shape[0] != closes.shape[1]:
        raise ValueError("weights length must match number of symbols")
    if dividend_yields is not None:
        dividend_yields = np.asarray(dividend_yields, dtype=float).ravel()
        if dividend_yields.shape[0] != closes.shape[1]:
            raise ValueError("dividend_yields length must match number of symbols")
    cost_model = cost_model or CostModel()

    spread_close = spread_mod.build_spread(closes, weights)
    z = spread_mod.rolling_zscore(spread_close, lookback=lookback)

    half_life = spread_mod.ou_half_life(spread_close)
    time_stop_bars = (time_stop_mult * half_life
                      if np.isfinite(half_life) else float("inf"))

    result = SimResult(half_life=half_life, time_stop_bars=time_stop_bars,
                       n_bars=len(closes))

    # First bar at which the z-score window is full (signals become valid).
    valid = z.notna()
    if not valid.any():
        return result
    start = int(np.argmax(valid.to_numpy()))

    # Fixed share scale from the basket gross notional at the first tradeable bar.
    first_prices = closes.iloc[start].to_numpy(dtype=float)
    gross_per_unit = float(np.sum(np.abs(weights * first_prices)))
    if gross_per_unit <= 0:
        return result
    scale = gross_notional / gross_per_unit
    result.share_scale = scale
    shares = scale * weights  # signed per-leg shares for a long-spread position

    zv = z.to_numpy()
    index = closes.index

    position = None  # None when flat, else a dict describing the open trade

    # Iterate to len-2: every action fills on the *next* bar's open.
    for i in range(start, len(closes) - 1):
        zi = zv[i]
        if np.isnan(zi):
            continue
        fill_row = opens.iloc[i + 1]
        fill_spread = _spread_at(fill_row, weights)
        fill_date = index[i + 1]

        if position is None:
            direction = 0
            if zi <= -z_enter:
                direction = +1
            elif zi >= z_enter:
                direction = -1
            if direction != 0:
                leg_shares = direction * shares
                leg_prices = fill_row.to_numpy(dtype=float)
                entry_cost = cost_model.entry_exit_cost(leg_shares, leg_prices)
                position = {
                    "direction": direction,
                    "entry_idx": i + 1,
                    "entry_date": fill_date,
                    "entry_spread": fill_spread,
                    "entry_cost": entry_cost,
                    "leg_shares": leg_shares,
                    "leg_prices_entry": leg_prices,
                }
            continue

        # In a position — evaluate exits on this bar's close, fill next open.
        direction = position["direction"]
        bars_held = (i + 1) - position["entry_idx"]
        reason = None
        if abs(zi) <= z_exit:
            reason = "reversion"
        elif direction == +1 and zi <= -z_stop:
            reason = "z_stop"
        elif direction == -1 and zi >= z_stop:
            reason = "z_stop"
        elif bars_held >= time_stop_bars:
            reason = "time_stop"

        if reason is not None:
            result.trades.append(
                _close_trade(position, fill_spread, fill_row, fill_date,
                             bars_held, reason, cost_model, dividend_yields))
            position = None

    # Force-close any still-open position at the last bar's close, mark-to-market.
    if position is not None:
        last_idx = len(closes) - 1
        last_date = index[last_idx]
        last_row = closes.iloc[last_idx]
        last_spread = _spread_at(last_row, weights)
        bars_held = last_idx - position["entry_idx"]
        result.trades.append(
            _close_trade(position, last_spread, last_row, last_date,
                         bars_held, "forced_eod", cost_model, dividend_yields))

    return result


def _close_trade(position, exit_spread, exit_row, exit_date, bars_held,
                 reason, cost_model: CostModel, dividend_yields=None) -> Trade:
    direction = position["direction"]
    leg_shares = position["leg_shares"]
    exit_prices = exit_row.to_numpy(dtype=float)
    exit_cost = cost_model.entry_exit_cost(leg_shares, exit_prices)

    calendar_days = max(0, (pd.Timestamp(exit_date) - pd.Timestamp(position["entry_date"])).days)
    # Financing accrues on the short legs' notional at entry prices; the signed
    # dividend overlay uses the same fixed entry prices and per-leg yields.
    holding_cost = cost_model.holding_cost(
        leg_shares, position["leg_prices_entry"], calendar_days,
        leg_yields=dividend_yields)

    gross_pnl = float(np.dot(leg_shares, exit_prices - position["leg_prices_entry"]))
    entry_cost = position["entry_cost"]
    net_pnl = gross_pnl - entry_cost - exit_cost - holding_cost

    return Trade(
        direction=direction,
        entry_date=position["entry_date"],
        exit_date=exit_date,
        entry_spread=position["entry_spread"],
        exit_spread=exit_spread,
        bars_held=int(bars_held),
        calendar_days=int(calendar_days),
        gross_pnl=gross_pnl,
        entry_cost=entry_cost,
        exit_cost=exit_cost,
        holding_cost=holding_cost,
        net_pnl=net_pnl,
        exit_reason=reason,
    )
