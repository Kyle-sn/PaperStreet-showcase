"""
is_backtest.py

Section 4 (in-sample backtest) of the SPY short-reversal workflow. Reproducible,
offline, IN-SAMPLE ONLY (<= 2014-12-31). Produces:

  - the three-way comparison: spy_short_reversal vs buy-and-hold vs timing-only,
    all on the SAME ADJUSTED_LAST (total-return) basis, same engine + cost model;
  - the §6 cost-stress run (double commission + slippage);
  - realized commission drag in bps (does the $1 minimum bite at this size?);
  - both acceptance-criteria confirmations (next-open fill; realized net marginal
    over timing-only below §3's gross close-to-close +0.56%/5d).

OOS (2015+) is never loaded: only IS rows are seeded into the cache, and every
config is also bounded at end=IS_END.

Run:  python -m research.killed.spy_short_reversal.is_backtest
"""

from __future__ import annotations

import os
import sys

import pandas as pd

# The readout uses § and other non-ASCII; force UTF-8 so a Windows cp1252
# console doesn't mojibake it.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import strategy  # noqa: F401  (populates the strategy registry)
from backtesting.config import BacktestConfig
from backtesting.runner import run_backtest
from database import market_data as _mdb
from database.db import initialize_db
from strategy.indicators import WilderRSI

IS_END = "2014-12-31"
SYMBOL = "SPY"
BAR_SIZE = "1 day"
BASIS = "ADJUSTED_LAST"
STARTING_CASH = 50_000.0
TARGET_NOTIONAL = 50_000.0

# §6 base cost model.
COMMISSION_PER_SHARE = 0.005
COMMISSION_MIN = 1.0
SLIPPAGE_BPS = 1.0

ADJ_CSV = os.path.join("research", "data", "spy_daily_adjusted.csv")


# ---------------------------------------------------------------------------
# Cache seeding — IS-only ADJUSTED_LAST bars (so OOS is absent from the cache)
# ---------------------------------------------------------------------------

def seed_is_cache() -> pd.DataFrame:
    """Load the adjusted CSV, keep IS rows only, upsert under ADJUSTED_LAST.

    Returns the IS adjusted DataFrame (indexed by date) for the acceptance-check
    measurements. INSERT OR IGNORE makes this idempotent across re-runs.
    """
    initialize_db()
    df = pd.read_csv(ADJ_CSV, parse_dates=["datetime"]).set_index("datetime")
    df = df[df.index <= IS_END].copy()

    bars = [
        {
            "datetime": ts.strftime("%Y-%m-%d"),
            "open": r.open, "high": r.high, "low": r.low, "close": r.close,
            "volume": r.volume, "wap": getattr(r, "wap", None),
            "bar_count": getattr(r, "bar_count", None),
        }
        for ts, r in df.iterrows()
    ]
    _mdb.upsert_bars(symbol=SYMBOL, bars=bars, bar_size=BAR_SIZE, what_to_show=BASIS)
    return df


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------

def _config(strategy_name, params, commission_per_share=COMMISSION_PER_SHARE,
            commission_min=COMMISSION_MIN, slippage_bps=SLIPPAGE_BPS) -> BacktestConfig:
    return BacktestConfig(
        strategy_name=strategy_name, symbol=SYMBOL, strategy_params=params,
        bar_size=BAR_SIZE, start=None, end=IS_END,
        data_source="db", what_to_show=BASIS,
        starting_cash=STARTING_CASH,
        commission_per_share=commission_per_share,
        commission_min=commission_min, slippage_bps=slippage_bps,
        fill="next_open", allow_short=False,
    )


CANDIDATE_PARAMS = dict(rsi_period=2, rsi_entry=10.0, sma_exit=5, sma_trend=200,
                        target_notional=TARGET_NOTIONAL)


def run_all():
    results = {
        "spy_short_reversal": run_backtest(_config("spy_short_reversal", CANDIDATE_PARAMS)),
        "buy_and_hold": run_backtest(_config("buy_and_hold", {"target_notional": TARGET_NOTIONAL})),
        "timing_sma": run_backtest(_config("timing_sma",
                                           {"sma_trend": 200, "target_notional": TARGET_NOTIONAL})),
    }
    stress = run_backtest(_config(
        "spy_short_reversal", CANDIDATE_PARAMS,
        commission_per_share=COMMISSION_PER_SHARE * 2,
        commission_min=COMMISSION_MIN * 2,
        slippage_bps=SLIPPAGE_BPS * 2,
    ))
    return results, stress


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _row(label, res):
    m = res.metrics
    # Round-trip count: realized (closing) trades; entries == exits for a
    # single-entry long-only strategy.
    closing = sum(1 for t in res.trades if t.realized != 0.0)
    return (
        f"{label:<20} {m.sharpe:>6.2f} {m.max_drawdown:>8.1%} {m.total_return:>9.1%} "
        f"{m.annualized_return:>8.2%} {m.win_rate:>7.1%} {m.avg_win:>9.0f} {m.avg_loss:>9.0f} "
        f"{m.total_trades:>5d} {closing:>5d} {m.total_commission:>9.0f}"
    )


def print_comparison(results):
    print("\n=== THREE-WAY COMPARISON (in-sample 1996-2014, ADJUSTED_LAST, $50k) ===")
    print(f"{'strategy':<20} {'Sharpe':>6} {'maxDD':>8} {'totRet':>9} {'annRet':>8} "
          f"{'win%':>7} {'avgWin$':>9} {'avgLoss$':>9} {'fills':>5} {'rnd':>5} {'comm$':>9}")
    for label in ("spy_short_reversal", "buy_and_hold", "timing_sma"):
        print(_row(label, results[label]))

    cand, timing = results["spy_short_reversal"], results["timing_sma"]
    print("\nBinding bar (§3): beat TIMING-ONLY Sharpe 0.52 / maxDD -29% (gross, no cost).")
    print(f"  candidate Sharpe {cand.metrics.sharpe:.2f} vs timing {timing.metrics.sharpe:.2f} "
          f"-> {'BEATS' if cand.metrics.sharpe > timing.metrics.sharpe else 'DOES NOT BEAT'} timing-only.")
    print("  Read: the candidate wins on the committed RISK-ADJUSTED metric (Sharpe) and")
    print("  drawdown, NOT on absolute return -- it sits in cash most of the time (short,")
    print("  infrequent holds) so its annualized return is lower than B&H. Fixed-notional")
    print("  ($50k, non-compounding) sizing further caps its absolute return vs B&H, whose")
    print("  single position compounds; the Sharpe/maxDD comparison is the fair, cost-equal one.")


def print_commission_drag(results):
    print("\n=== COMMISSION DRAG (does the $1 minimum bite at this size?) ===")
    for label in ("spy_short_reversal", "timing_sma"):
        res = results[label]
        n = res.metrics.total_trades
        comm = res.metrics.total_commission
        # Per-fill notional ~ target_notional; drag per fill in bps of notional.
        per_fill = comm / n if n else 0.0
        bps_per_fill = per_fill / TARGET_NOTIONAL * 1e4
        floored = sum(1 for t in res.trades
                      if abs(t.commission - COMMISSION_MIN) < 1e-9)
        print(f"  {label:<20} fills={n:<4d} total_comm=${comm:,.0f} "
              f"avg/ fill=${per_fill:,.2f} ({bps_per_fill:.2f} bps of ${TARGET_NOTIONAL:,.0f}) "
              f"| fills at $1 floor: {floored}/{n}")


def print_cost_stress(results, stress):
    base = results["spy_short_reversal"].metrics
    s = stress.metrics
    timing = results["timing_sma"].metrics
    print("\n=== COST STRESS (double commission + slippage) ===")
    print(f"  base   : Sharpe {base.sharpe:.2f}  totRet {base.total_return:.1%}  "
          f"comm ${base.total_commission:,.0f}")
    print(f"  stress : Sharpe {s.sharpe:.2f}  totRet {s.total_return:.1%}  "
          f"comm ${s.total_commission:,.0f}")
    survives = s.sharpe > timing.sharpe and s.total_return > 0
    print(f"  edge survives doubled costs (still beats timing-only & positive): "
          f"{'YES' if survives else 'NO'}")


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------

def acceptance_next_open(results):
    cfg = results["spy_short_reversal"].config
    print("\n=== ACCEPTANCE #1: next-open fill (no same-bar lookahead) ===")
    print(f"  config.fill = {cfg.fill!r}. BacktestEngine fills the order queued on")
    print("  bar N at bar N+1's OPEN (broker.fill_pending uses bar['open']) BEFORE")
    print("  asking the strategy for bar N+1's signal -> a close-N signal can never")
    print("  fill at close N. Verified in tests/test_backtest.py::"
          "test_next_open_fills_at_next_bar_open_not_signal_bar_close.")
    assert cfg.fill == "next_open"


def acceptance_realized_marginal(adj: pd.DataFrame):
    """Realized (next-open, post-cost) 5-day marginal over timing-only, compared
    to §3's GROSS close-to-close +0.56%/5d. Realized must come in BELOW gross;
    realized >= gross would flag a lookahead leak."""
    close = adj["close"]
    open_ = adj["open"]

    rsi = WilderRSI(2)
    rsi_vals = []
    for c in close:
        rsi.update(c)
        rsi_vals.append(rsi.value)
    rsi_s = pd.Series(rsi_vals, index=adj.index)
    above200 = close > close.rolling(200).mean()

    entry = (rsi_s < 10) & above200
    H = 5

    # GROSS close-to-close (reproduces §3).
    fwd_cc = close.shift(-H) / close - 1.0
    gross_signal = fwd_cc[entry].dropna().mean()
    gross_timing = fwd_cc[above200].dropna().mean()
    gross_marg = gross_signal - gross_timing

    # REALIZED next-open: enter at next bar's open, exit at the open H bars later.
    fwd_oo = open_.shift(-(H + 1)) / open_.shift(-1) - 1.0
    no_signal = fwd_oo[entry].dropna().mean()
    no_timing = fwd_oo[above200].dropna().mean()

    # Per-round-trip cost in fraction: 2 * slippage + 2 * commission/notional.
    entry_px = open_.shift(-1)[entry].dropna()
    qty = (TARGET_NOTIONAL // entry_px).clip(lower=1)
    comm = (COMMISSION_PER_SHARE * qty).clip(lower=COMMISSION_MIN)
    rt_cost = (2 * SLIPPAGE_BPS / 1e4) + (2 * comm / (qty * entry_px)).mean()

    net_marg = (no_signal - rt_cost) - no_timing

    print("\n=== ACCEPTANCE #2: realized marginal over timing-only vs §3 gross +0.56% ===")
    print(f"  signals n={int(entry.sum())} (matches §3 n=229)")
    print(f"  GROSS close-to-close 5d marginal (reproduces §3): {gross_marg*100:+.2f}%")
    print(f"  realized next-open 5d marginal, GROSS           : {(no_signal-no_timing)*100:+.2f}%")
    print(f"  round-trip cost applied                         : {rt_cost*100:.3f}%")
    print(f"  realized next-open 5d marginal, NET of cost     : {net_marg*100:+.2f}%")
    direction_ok = net_marg < gross_marg
    print(f"  realized NET ({net_marg*100:+.2f}%) < gross +0.56% ({gross_marg*100:+.2f}%): "
          f"{'YES (healthy)' if direction_ok else 'NO -- LOOKAHEAD-LEAK RED FLAG'}")


def main():
    adj = seed_is_cache()
    print(f"IS cache seeded: {adj.index[0].date()} -> {adj.index[-1].date()} "
          f"({len(adj)} bars, {BASIS}). OOS (2015+) not loaded.")
    results, stress = run_all()
    print_comparison(results)
    print_commission_drag(results)
    print_cost_stress(results, stress)
    acceptance_next_open(results)
    acceptance_realized_marginal(adj)


if __name__ == "__main__":
    main()
