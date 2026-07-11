"""
sensitivity.py

Section 5 (parameter sensitivity) of the SPY short-reversal workflow, plus the
Task-0 cash / risk-free correction that §5 depends on. Reproducible, offline,
IN-SAMPLE ONLY (<= 2014-12-31). The FROZEN spec is never re-picked here — §5 only
asks whether the frozen point sits on a PLATEAU or a SPIKE.

Produces:
  - TASK 0: the §4 three-way table recomputed two ways, consistently across all
    three legs: (a) idle cash at 0% (current convention) and (b) idle cash
    credited at a realized short-rate series (3M T-bill annual averages). Shows
    that the Sharpe RANKING is invariant to the convention (Sharpe is ~invariant
    when rf matches the cash rate) while ABSOLUTE return moves materially for the
    cash-heavy candidate. Surfaces the delta for sign-off; does NOT switch the
    default.
  - TASK 1: one-axis-at-a-time sensitivity sweeps around the frozen point and a
    2D grid (entry threshold x trend-filter length), each with a low-trade flag.
  - OPTIONAL: an information-only hard-stop variant (exit also on close < SMA200),
    NOT a spec change.

OOS (2015+) is never loaded: the cache is seeded IS-only and every config is
bounded at end=IS_END.

Run:  python -m research.killed.spy_short_reversal.sensitivity
"""

from __future__ import annotations

import sys
from collections import defaultdict

import pandas as pd

# § and other non-ASCII in the readout; force UTF-8 on a cp1252 console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import strategy  # noqa: F401  (populates the strategy registry)
from backtesting.config import BacktestConfig
from backtesting.runner import run_backtest
from strategy.base_strategy import BaseStrategy
from strategy.registry import register_strategy
from strategy.signal import OrderRequest
from strategy.parked.spy_short_reversal import SpyShortReversalStrategy

# Reuse the §4 cache seeding and constants verbatim so the sweep rides the exact
# same path the §4 gate ran on (no reimplementation).
from research.killed.spy_short_reversal.is_backtest import (
    BAR_SIZE,
    BASIS,
    COMMISSION_MIN,
    COMMISSION_PER_SHARE,
    IS_END,
    SLIPPAGE_BPS,
    STARTING_CASH,
    SYMBOL,
    TARGET_NOTIONAL,
    seed_is_cache,
)

FROZEN = dict(rsi_period=2, rsi_entry=10.0, sma_exit=5, sma_trend=200,
              target_notional=TARGET_NOTIONAL)

# ---------------------------------------------------------------------------
# Realized short-rate series (Task 0, convention b)
# ---------------------------------------------------------------------------
# 3-Month Treasury Bill, secondary-market rate, ANNUAL AVERAGE in percent
# (FRED series TB3MS). Hardcoded because the research environment has no network
# (same reason the §2 data is single-source IBKR). Annual granularity is ample
# for crediting idle cash on daily bars. These are the realized cash rates an
# uninvested balance would actually have earned across the IS window.
TB3MS_ANNUAL_PCT = {
    1996: 5.02, 1997: 5.07, 1998: 4.81, 1999: 4.66, 2000: 5.85,
    2001: 3.45, 2002: 1.62, 2003: 1.02, 2004: 1.38, 2005: 3.16,
    2006: 4.73, 2007: 4.36, 2008: 1.37, 2009: 0.15, 2010: 0.14,
    2011: 0.05, 2012: 0.09, 2013: 0.06, 2014: 0.03,
    # OOS years (§7). 2015-2023 are FRED TB3MS annual averages; 2024-2026 are
    # approximate (2024 ~5.0 falling late-year; 2025/2026 estimated ~4.3/4.0).
    # The elevated 2023-2025 cash rates are a genuine, realized tailwind for a
    # strategy that sits in cash ~90% of the time — fair to credit under (b).
    2015: 0.05, 2016: 0.32, 2017: 0.93, 2018: 1.94, 2019: 2.11,
    2020: 0.36, 2021: 0.04, 2022: 2.07, 2023: 5.14, 2024: 5.02,
    2025: 4.30, 2026: 4.00,
}
_PPY = 252  # daily-bar annualization, matches backtesting.metrics


def _daily_rate(year: int) -> float:
    """Per-bar (daily) compounding rate from the annual T-bill average."""
    annual = TB3MS_ANNUAL_PCT.get(year, 0.0) / 100.0
    return (1.0 + annual) ** (1.0 / _PPY) - 1.0


# ---------------------------------------------------------------------------
# Config builder (IS-only, db-source, ADJUSTED_LAST) — same as §4
# ---------------------------------------------------------------------------

def _config(strategy_name: str, params: dict) -> BacktestConfig:
    return BacktestConfig(
        strategy_name=strategy_name, symbol=SYMBOL, strategy_params=params,
        bar_size=BAR_SIZE, start=None, end=IS_END,
        data_source="db", what_to_show=BASIS,
        starting_cash=STARTING_CASH,
        commission_per_share=COMMISSION_PER_SHARE,
        commission_min=COMMISSION_MIN, slippage_bps=SLIPPAGE_BPS,
        fill="next_open", allow_short=False,
    )


# ===========================================================================
# TASK 0 — cash / risk-free treatment
# ===========================================================================

def _key(ts) -> str:
    """Normalize a bar/trade datetime to a YYYY-MM-DD key for joining."""
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def reconstruct_with_cash_interest(result, close_by_key: dict, credit_interest: bool):
    """Rebuild the equity curve from the trade log, optionally crediting idle
    cash at the realized short rate.

    Returns (equity_list, bar_rf_list, frac_in_cash).

    With credit_interest=False this MUST reproduce the engine's equity curve
    exactly (idle cash at 0%) — asserted by the caller — which validates the
    reconstruction before we trust the credited variant. `bar_rf_list[i]` is the
    per-bar cash rate aligned to returns (i.e. for bar i>=1), used as rf in the
    excess-over-cash Sharpe so the convention stays self-consistent.
    """
    trades_by_key: dict[str, list] = defaultdict(list)
    for t in result.trades:
        trades_by_key[_key(t.datetime)].append(t)

    cash = result.config.starting_cash
    position = 0.0
    equity: list[float] = []
    bar_rf: list[float] = []
    in_cash_bars = 0

    for ts in result.timestamps:
        k = _key(ts)
        daily = _daily_rate(pd.Timestamp(ts).year) if credit_interest else 0.0
        cash *= (1.0 + daily)  # one bar of interest on the cash held
        for t in trades_by_key.get(k, []):
            signed = t.quantity if t.action == "BUY" else -t.quantity
            cash -= t.price * signed
            cash -= t.commission
            position += signed
        equity.append(cash + position * close_by_key[k])
        bar_rf.append(daily)
        if position == 0:
            in_cash_bars += 1

    frac_in_cash = in_cash_bars / len(result.timestamps)
    # bar_rf for returns is the rate over each step i-1 -> i, i.e. drop the first.
    return equity, bar_rf[1:], frac_in_cash


def _metrics_from_equity(equity: list[float], bar_rf: list[float]):
    """Total / annualized return, maxDD, and excess-over-cash Sharpe from a raw
    equity curve plus a per-bar cash-rate series (rf). bar_rf may be all-zeros
    (convention a) -> ordinary rf=0 Sharpe."""
    n = len(equity) - 1
    total = equity[-1] / equity[0] - 1.0
    growth = equity[-1] / equity[0]
    annual = growth ** (_PPY / n) - 1.0 if growth > 0 else -1.0

    peak, maxdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            maxdd = min(maxdd, v / peak - 1.0)

    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity))]
    excess = [r - rf for r, rf in zip(rets, bar_rf)]
    m = sum(excess) / len(excess)
    var = sum((e - m) ** 2 for e in excess) / len(excess)
    std = var ** 0.5
    sharpe = (m / std) * (_PPY ** 0.5) if std > 0 else 0.0
    return total, annual, maxdd, sharpe


def task0_cash_conventions(results, close_by_key):
    legs = ("spy_short_reversal", "timing_sma", "buy_and_hold")

    print("\n" + "=" * 78)
    print("TASK 0 — CASH / RISK-FREE TREATMENT (idle cash convention)")
    print("=" * 78)
    print("Current state: backtesting.metrics._sharpe used rf=0; Portfolio.mark holds")
    print("idle cash at 0%. That pair is SELF-CONSISTENT ('excess over cash' where")
    print("cash=0%), not a bug. Below: the §4 three-way table both ways, same engine")
    print("and cost model across all three legs.\n")

    # --- validate the reconstruction reproduces the engine curve at 0% ---
    rebuilt = {}
    for name in legs:
        eq0, rf0, frac = reconstruct_with_cash_interest(results[name], close_by_key,
                                                        credit_interest=False)
        eng = results[name].equity
        max_abs = max(abs(a - b) for a, b in zip(eq0, eng))
        assert max_abs < 1e-6, f"{name}: reconstruction != engine ({max_abs})"
        rebuilt[name] = frac
    print(f"  reconstruction check (credit=0%): matches engine equity to <1e-6 for "
          f"all three legs.\n")

    hdr = (f"{'strategy':<20} {'Sharpe':>6} {'maxDD':>8} {'totRet':>9} {'annRet':>8} "
           f"{'cash%':>6}")

    print("(a) IDLE CASH @ 0%  (current convention; rf=0)")
    print(hdr)
    table_a = {}
    for name in legs:
        eq, rf, frac = reconstruct_with_cash_interest(results[name], close_by_key,
                                                      credit_interest=False)
        tot, ann, dd, shp = _metrics_from_equity(eq, rf)
        table_a[name] = (shp, dd, tot, ann)
        print(f"{name:<20} {shp:>6.2f} {dd:>8.1%} {tot:>9.1%} {ann:>8.2%} {frac:>6.1%}")

    print("\n(b) IDLE CASH @ 3M T-BILL  (credited; rf = same series, excess-over-cash)")
    print(hdr)
    table_b = {}
    for name in legs:
        eq, rf, frac = reconstruct_with_cash_interest(results[name], close_by_key,
                                                      credit_interest=True)
        tot, ann, dd, shp = _metrics_from_equity(eq, rf)
        table_b[name] = (shp, dd, tot, ann)
        print(f"{name:<20} {shp:>6.2f} {dd:>8.1%} {tot:>9.1%} {ann:>8.2%} {frac:>6.1%}")

    print("\nDelta (b)-(a):")
    print(f"{'strategy':<20} {'dSharpe':>8} {'dAnnRet':>8}")
    for name in legs:
        d_s = table_b[name][0] - table_a[name][0]
        d_a = table_b[name][3] - table_a[name][3]
        print(f"{name:<20} {d_s:>+8.2f} {d_a:>+8.2%}")

    def ranked(tbl):
        return sorted(legs, key=lambda n: tbl[n][0], reverse=True)

    print("\nSharpe ranking (a):", " > ".join(ranked(table_a)))
    print("Sharpe ranking (b):", " > ".join(ranked(table_b)))
    holds = (ranked(table_a) == ranked(table_b)
             and ranked(table_a)[0] == "spy_short_reversal")
    print(f"Ranking spy_short_reversal > timing_sma > buy_and_hold HOLDS both ways: "
          f"{'YES' if holds else 'NO'}")
    print("Read: Sharpe is ~invariant to the cash convention (rf tracks the credited")
    print("rate); the candidate's ANNRET rises materially under (b) because it sits in")
    print("cash ~95% of the time, while B&H barely moves (always invested). The cash")
    print("convention is the dominant lever for ABSOLUTE return, not for Sharpe.")
    print("\nNOT silently adopted: default stays rf=0 / cash@0% pending sign-off.")
    return table_a, table_b


# ===========================================================================
# TASK 1 — sensitivity sweep
# ===========================================================================

def run_cell(params: dict) -> dict:
    res = run_backtest(_config("spy_short_reversal", params))
    m = res.metrics
    rounds = sum(1 for t in res.trades if t.realized != 0.0)
    return dict(sharpe=m.sharpe, maxdd=m.max_drawdown, totret=m.total_return,
                annret=m.annualized_return, win=m.win_rate, rounds=rounds)

# Round-trips below this are too few for the Sharpe to be meaningful (flagged).
MIN_ROUNDS = 40


def _cell_line(label, c, frozen_flag):
    flag = " <-FROZEN" if frozen_flag else ""
    thin = " [THIN]" if c["rounds"] < MIN_ROUNDS else ""
    return (f"  {label:<10} {c['sharpe']:>6.2f} {c['maxdd']:>8.1%} {c['totret']:>9.1%} "
            f"{c['annret']:>8.2%} {c['win']:>7.1%} {c['rounds']:>5d}{flag}{thin}")


def _axis_header():
    return (f"  {'value':<10} {'Sharpe':>6} {'maxDD':>8} {'totRet':>9} {'annRet':>8} "
            f"{'win%':>7} {'rnd':>5}")


def sweep_axis(name, axis_key, values, frozen_val):
    print(f"\n--- axis: {name} (frozen = {frozen_val}) ---")
    print(_axis_header())
    for v in values:
        params = dict(FROZEN)
        params[axis_key] = v
        c = run_cell(params)
        print(_cell_line(str(v), c, v == frozen_val))


def grid_2d(entries, trends):
    print(f"\n--- 2D grid: rsi_entry (rows) x sma_trend (cols) — Sharpe / round-trips ---")
    print("           " + "".join(f"{t:>14}" for t in trends))
    for e in entries:
        cells = []
        for t in trends:
            params = dict(FROZEN)
            params["rsi_entry"] = float(e)
            params["sma_trend"] = t
            c = run_cell(params)
            frozen = (e == FROZEN["rsi_entry"] and t == FROZEN["sma_trend"])
            mark = "*" if frozen else (" " if c["rounds"] >= MIN_ROUNDS else "!")
            cells.append(f"{c['sharpe']:>7.2f}/{c['rounds']:<4d}{mark}")
        print(f"  rsi<{e:<5}" + "".join(f"{x:>14}" for x in cells))
    print("  legend: Sharpe/round-trips; * = frozen point, ! = thin (<%d round-trips)"
          % MIN_ROUNDS)


def task1_sensitivity():
    print("\n" + "=" * 78)
    print("TASK 1 — PARAMETER SENSITIVITY (frozen spec, IS only; spec NOT re-picked)")
    print("=" * 78)
    print("Frozen: RSI(2)<10 & close>SMA200, exit close>SMA5, single-entry, long-only.")
    print(f"Sharpe shown under convention (a) (rf=0); Sharpe is convention-invariant.")
    print(f"[THIN] flags cells with < {MIN_ROUNDS} round-trips (Sharpe not meaningful).")

    sweep_axis("RSI entry threshold", "rsi_entry", [5.0, 10.0, 15.0, 20.0], 10.0)
    sweep_axis("RSI period", "rsi_period", [2, 3, 4], 2)
    sweep_axis("Exit SMA", "sma_exit", [3, 5, 10], 5)
    sweep_axis("Trend-filter SMA", "sma_trend", [100, 150, 200, 250], 200)
    grid_2d([5, 10, 15, 20], [100, 150, 200, 250])


# ===========================================================================
# OPTIONAL — information-only hard-stop variant (NOT a spec change)
# ===========================================================================

@register_strategy("spy_short_reversal_stopexit")
class _StopExitVariant(SpyShortReversalStrategy):
    """Frozen spec PLUS a hard exit on close < SMA(trend). Information-only: it
    probes how much of the benign §4 maxDD rests on the entry filter alone vs an
    explicit stop. NOT adopted into the frozen spec this session."""

    def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None:
        close = bar["close"]
        self.rsi.update(close)
        self.exit_sma.append(close)
        self.trend_sma.append(close)
        if not (self.rsi.ready and self.exit_sma.ready and self.trend_sma.ready):
            return None
        if position > 0:
            # Bounce exit OR hard stop below the trend filter.
            if close > self.exit_sma.mean() or close < self.trend_sma.mean():
                return self.sell(position)
            return None
        if position <= 0 and self.rsi.value < self.rsi_entry and close > self.trend_sma.mean():
            qty = int(self.target_notional // close)
            return self.buy(qty) if qty > 0 else None
        return None


def _row_from_result(res) -> dict:
    m = res.metrics
    return dict(sharpe=m.sharpe, maxdd=m.max_drawdown, totret=m.total_return,
                annret=m.annualized_return, win=m.win_rate,
                rounds=sum(1 for t in res.trades if t.realized != 0.0))


def optional_stop_exit(base_result):
    print("\n" + "=" * 78)
    print("OPTIONAL (info-only) — hard stop on close < SMA200 (NOT a spec change)")
    print("=" * 78)
    stop = run_backtest(_config("spy_short_reversal_stopexit", dict(FROZEN)))
    print(_axis_header())
    print(_cell_line("frozen", _row_from_result(base_result), False))
    print(_cell_line("+stop", _row_from_result(stop), False))
    print("Read: a hard stop should rarely fire IS (the 200-day entry gate already keeps")
    print("entries inside uptrends and IS dodged 2008); informs a possible future spec.")


# ===========================================================================

def main():
    adj = seed_is_cache()
    print(f"IS cache seeded: {adj.index[0].date()} -> {adj.index[-1].date()} "
          f"({len(adj)} bars, {BASIS}). OOS (2015+) not loaded.")
    close_by_key = {_key(ts): c for ts, c in zip(adj.index, adj["close"])}

    base = {
        "spy_short_reversal": run_backtest(_config("spy_short_reversal", dict(FROZEN))),
        "buy_and_hold": run_backtest(_config("buy_and_hold", {"target_notional": TARGET_NOTIONAL})),
        "timing_sma": run_backtest(_config("timing_sma",
                                           {"sma_trend": 200, "target_notional": TARGET_NOTIONAL})),
    }

    task0_cash_conventions(base, close_by_key)
    task1_sensitivity()
    optional_stop_exit(base["spy_short_reversal"])


if __name__ == "__main__":
    main()
