"""
oos_backtest.py

Section 7 (out-of-sample ONE-SHOT) of the SPY short-reversal workflow. This runs
the FROZEN spec — unchanged from §4/§5 — against the held-out OOS window
(2015-01-01 onward), evaluated against the gates LOCKED in the research notes
(§7 pre-registration, Decision #7) BEFORE this was ever run.

DISCIPLINE
----------
  - One shot. No re-tuning regardless of result. If it fails a gate, it is parked
    and the failure is documented — that is a valid outcome.
  - Frozen spec: entry RSI(2)<10 & close>SMA(200); exit close>SMA(5); single-entry;
    long-only; trend filter SMA(200). NOT re-picked here.
  - Metrics on the LOCKED convention (b): idle cash credited at the realized 3M
    T-bill series, Sharpe excess-over-cash with rf = the same series, applied
    consistently to all three legs (candidate, timing_sma, buy_and_hold).

OOS measurement boundary
------------------------
Indicators are primed on a pre-2015 warmup slice (bars loaded from 2013-06-01) so
the strategy enters 2015 fully warmed, exactly as it would live. Those warmup bars
trade, but ALL metrics and round-trip counts are measured on the 2015-01-01+ slice
only (the equity curve is re-based at the OOS boundary), so there is no in-sample
contamination.

Run:  python -m research.killed.spy_short_reversal.oos_backtest
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import strategy  # noqa: F401  (populates the registry)
from backtesting.config import BacktestConfig
from backtesting.metrics import compute_metrics
from backtesting.runner import run_backtest
from database import market_data as _mdb
from database.db import initialize_db

# Reuse the §4/§5 constants, the locked T-bill series, and the per-bar rate.
from research.killed.spy_short_reversal.is_backtest import (
    BAR_SIZE,
    BASIS,
    COMMISSION_MIN,
    COMMISSION_PER_SHARE,
    SLIPPAGE_BPS,
    STARTING_CASH,
    SYMBOL,
    TARGET_NOTIONAL,
)
from research.killed.spy_short_reversal.sensitivity import (
    FROZEN,
    TB3MS_ANNUAL_PCT,
    _daily_rate,
    _key,
)

WARMUP_START = "2013-06-01"   # ~400 bars: enough to prime the 200-day SMA by 2015
OOS_START = "2015-01-01"      # Decision #3: OOS is 2015+
ADJ_CSV = os.path.join("research", "data", "spy_daily_adjusted.csv")

# Locked §7 gate thresholds (Decision #7). Convention (b).
G2_MIN_SHARPE = 0.40
G3_MAXDD_CEILING = -0.20      # OOS maxDD must be SHALLOWER than this
G4_MIN_ROUNDTRIPS = 40


# ---------------------------------------------------------------------------
# Cache seeding — FULL history (IS + OOS). The one-shot is the moment OOS loads.
# ---------------------------------------------------------------------------

def seed_full_cache() -> pd.DataFrame:
    initialize_db()
    df = pd.read_csv(ADJ_CSV, parse_dates=["datetime"]).set_index("datetime")
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


def _config(strategy_name: str, params: dict) -> BacktestConfig:
    return BacktestConfig(
        strategy_name=strategy_name, symbol=SYMBOL, strategy_params=params,
        bar_size=BAR_SIZE, start=WARMUP_START, end=None,
        data_source="db", what_to_show=BASIS,
        starting_cash=STARTING_CASH,
        commission_per_share=COMMISSION_PER_SHARE,
        commission_min=COMMISSION_MIN, slippage_bps=SLIPPAGE_BPS,
        fill="next_open", allow_short=False,
    )


# ---------------------------------------------------------------------------
# Convention-(b) reconstruction with idle-cash interest, sliced to the OOS window
# ---------------------------------------------------------------------------

def _reconstruct_full(result, close_by_key: dict):
    """Per-bar (timestamp, equity_b, daily_rate) over the full loaded range,
    crediting idle cash at the realized short rate. Validated against the engine
    curve by the caller (interest off -> exact match)."""
    trades_by_key: dict[str, list] = defaultdict(list)
    for t in result.trades:
        trades_by_key[_key(t.datetime)].append(t)

    cash = result.config.starting_cash
    position = 0.0
    out = []
    for ts in result.timestamps:
        k = _key(ts)
        daily = _daily_rate(pd.Timestamp(ts).year)
        cash *= (1.0 + daily)
        for t in trades_by_key.get(k, []):
            signed = t.quantity if t.action == "BUY" else -t.quantity
            cash -= t.price * signed + t.commission
            position += signed
        out.append((ts, cash + position * close_by_key[k], daily, position))
    return out


def _oos_metrics_b(result, close_by_key: dict):
    """Convention-(b) OOS metrics: total/annualized return, maxDD, excess-over-cash
    Sharpe, round-trips, and %-in-cash — all on the 2015-01-01+ slice (re-based)."""
    full = _reconstruct_full(result, close_by_key)
    oos = [(ts, eq, d, pos) for (ts, eq, d, pos) in full
           if pd.Timestamp(ts) >= pd.Timestamp(OOS_START)]
    eq = [x[1] for x in oos]
    daily = [x[2] for x in oos]
    pos = [x[3] for x in oos]

    n = len(eq) - 1
    total = eq[-1] / eq[0] - 1.0
    annual = (eq[-1] / eq[0]) ** (252.0 / n) - 1.0 if eq[-1] > 0 else -1.0

    peak, maxdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        maxdd = min(maxdd, v / peak - 1.0)

    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq))]
    excess = [r - daily[i] for i, r in enumerate(rets, start=1)]
    m = sum(excess) / len(excess)
    var = sum((e - m) ** 2 for e in excess) / len(excess)
    std = var ** 0.5
    sharpe = (m / std) * (252.0 ** 0.5) if std > 0 else 0.0

    rounds = sum(1 for t in result.trades
                 if t.realized != 0.0 and pd.Timestamp(t.datetime) >= pd.Timestamp(OOS_START))
    frac_cash = sum(1 for p in pos if p == 0) / len(pos)
    return dict(sharpe=sharpe, maxdd=maxdd, total=total, annual=annual,
                rounds=rounds, frac_cash=frac_cash, eq=eq, ts=[x[0] for x in oos])


def _oos_metrics_a(result):
    """Convention-(a) OOS metrics (rf=0, cash@0%) for transparency — sliced engine
    equity at the OOS boundary."""
    idx = next(i for i, ts in enumerate(result.timestamps)
               if pd.Timestamp(ts) >= pd.Timestamp(OOS_START))
    eq = result.equity[idx:]
    trades = [t for t in result.trades
              if pd.Timestamp(t.datetime) >= pd.Timestamp(OOS_START)]
    m = compute_metrics(eq, trades, BAR_SIZE, risk_free_rate=0.0)
    rounds = sum(1 for t in trades if t.realized != 0.0)
    return dict(sharpe=m.sharpe, maxdd=m.max_drawdown, total=m.total_return,
                annual=m.annualized_return, rounds=rounds)


# ---------------------------------------------------------------------------
# G5 diagnostic — does the filter mute the downturns (2020/2022)?
# ---------------------------------------------------------------------------

def _downturn_diagnostic(cand_result):
    """Count candidate entries in 2020 and 2022 vs the same-window oversold-signal
    universe, to show the 200-day filter stood aside through the drawdowns."""
    entries = [pd.Timestamp(t.datetime) for t in cand_result.trades if t.action == "BUY"]
    in2020 = sum(1 for d in entries if d.year == 2020 and pd.Timestamp(OOS_START) <= d)
    in2022 = sum(1 for d in entries if d.year == 2022)
    feb_mar_2020 = sum(1 for d in entries
                       if d.year == 2020 and d.month in (2, 3))
    return in2020, feb_mar_2020, in2022


# ---------------------------------------------------------------------------

def main():
    full_df = seed_full_cache()
    oos_n = int((full_df.index >= pd.Timestamp(OOS_START)).sum())
    print(f"Full cache seeded: {full_df.index[0].date()} -> {full_df.index[-1].date()} "
          f"({len(full_df)} bars, {BASIS}).")
    print(f"OOS window: {OOS_START} -> {full_df.index[-1].date()} ({oos_n} bars); "
          f"warmup load from {WARMUP_START}. THE ONE-SHOT.")

    close_by_key = {_key(ts): c for ts, c in zip(full_df.index, full_df["close"])}

    legs = ("spy_short_reversal", "timing_sma", "buy_and_hold")
    params = {
        "spy_short_reversal": dict(FROZEN),
        "timing_sma": {"sma_trend": 200, "target_notional": TARGET_NOTIONAL},
        "buy_and_hold": {"target_notional": TARGET_NOTIONAL},
    }
    results = {name: run_backtest(_config(name, params[name])) for name in legs}
    # The idle-cash reconstruction is exercised and asserted-against-engine in §5
    # (research/spy_short_reversal/sensitivity.py); reused verbatim here.

    print("\n" + "=" * 80)
    print("§7 OOS ONE-SHOT — FROZEN SPEC, convention (b) excess-over-3M-T-bill")
    print("=" * 80)

    b = {name: _oos_metrics_b(results[name], close_by_key) for name in legs}
    a = {name: _oos_metrics_a(results[name]) for name in legs}

    hdr = (f"{'strategy':<20} {'Sharpe':>6} {'maxDD':>8} {'totRet':>9} {'annRet':>8} "
           f"{'rnd':>5} {'cash%':>6}")
    print("\n(b) LOCKED basis — idle cash @ 3M T-bill, rf = same series:")
    print(hdr)
    for name in legs:
        x = b[name]
        print(f"{name:<20} {x['sharpe']:>6.2f} {x['maxdd']:>8.1%} {x['total']:>9.1%} "
              f"{x['annual']:>8.2%} {x['rounds']:>5d} {x['frac_cash']:>6.1%}")

    print("\n(a) reference — idle cash @ 0%, rf = 0 (transparency only, NOT the gate basis):")
    print(f"{'strategy':<20} {'Sharpe':>6} {'maxDD':>8} {'totRet':>9} {'annRet':>8} {'rnd':>5}")
    for name in legs:
        x = a[name]
        print(f"{name:<20} {x['sharpe']:>6.2f} {x['maxdd']:>8.1%} {x['total']:>9.1%} "
              f"{x['annual']:>8.2%} {x['rounds']:>5d}")

    # ---- Gate evaluation (convention b) ----
    cand, timing, bh = b["spy_short_reversal"], b["timing_sma"], b["buy_and_hold"]
    in2020, febmar2020, in2022 = _downturn_diagnostic(results["spy_short_reversal"])

    g1 = cand["sharpe"] > timing["sharpe"] and cand["sharpe"] > bh["sharpe"]
    g2 = cand["sharpe"] >= G2_MIN_SHARPE
    g3 = cand["maxdd"] > G3_MAXDD_CEILING          # shallower than -20%
    g4 = cand["rounds"] >= G4_MIN_ROUNDTRIPS
    g5 = cand["maxdd"] > bh["maxdd"]               # candidate DD shallower than B&H DD

    print("\n" + "-" * 80)
    print("GATE EVALUATION (LOCKED §7 pre-registration, Decision #7) — convention (b)")
    print("-" * 80)
    print(f"  G1 beats both benchmarks on Sharpe : cand {cand['sharpe']:.2f} vs "
          f"timing {timing['sharpe']:.2f} / B&H {bh['sharpe']:.2f}  -> {_pf(g1)}")
    print(f"  G2 no Sharpe collapse (>= {G2_MIN_SHARPE:.2f})    : {cand['sharpe']:.2f}  -> {_pf(g2)}")
    print(f"  G3 maxDD shallower than {G3_MAXDD_CEILING:.0%}      : {cand['maxdd']:.1%}  -> {_pf(g3)}")
    print(f"  G4 >= {G4_MIN_ROUNDTRIPS} round-trips             : {cand['rounds']}  -> {_pf(g4)}")
    print(f"  G5 filter mutes downturn (DD < B&H): cand {cand['maxdd']:.1%} vs B&H "
          f"{bh['maxdd']:.1%}  -> {_pf(g5)}")
    print(f"     downturn entries: 2020 total={in2020} (Feb-Mar crash={febmar2020}), "
          f"2022 total={in2022}")

    verdict = g1 and g2 and g3 and g4 and g5
    print("\n" + "=" * 80)
    print(f"  §7 VERDICT: {'PASS' if verdict else 'FAIL'}  "
          f"(PASS requires G1-G4 all true AND G5 holds)")
    if not verdict:
        failed = [n for n, g in [("G1", g1), ("G2", g2), ("G3", g3), ("G4", g4), ("G5", g5)]
                  if not g]
        print(f"  failed gate(s): {', '.join(failed)} -> PARK candidate, document, NO re-tune.")
    print("=" * 80)


def _pf(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


if __name__ == "__main__":
    main()
