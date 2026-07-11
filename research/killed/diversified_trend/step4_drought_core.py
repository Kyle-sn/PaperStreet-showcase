"""
Step 3 (IS backtest) -- drought-core sub-window confirmation, pre-kill.

NOT a new test, NOT a sweep, NOT OOS. This is a strict SUB-WINDOW of the existing
IS backtest (research.killed.diversified_trend.step4_backtest.run_backtest): same data,
same locked sizing (10% vol target, PIT 60d inverse-vol, integer contracts), same
cost haircut, same shift(2) lag. It only slices the already-computed basket NET
$-PnL series on calendar windows and reports Sharpe + max DD per window.

Purpose (per the 2026-06-21 Desktop gate review): put the secular-vs-drought
decomposition on the record at the PORTFOLIO level before the candidate is killed.
The gate diagnostics (step4_gate_diagnostics.py) already showed the two top return
legs (6J, ZN) die in 2016-2019 at the per-leg level; this confirms the BASKET does
the same.

Windows:
  - 2011-07..2015-12  secular run (Abenomics yen slide + EZ-crisis bond bull)
  - 2016-01..2019-12  drought core (the true trend-drought; closest analogue to the
                      expected hostile 2024-2028 forward regime)
  - 2011-07..2019-12  full base case (== the load-bearing gate window) for reference

NOTE on the "2011-2019 conservative base case" label: the Step 3 doc treated all of
2011-2019 as the conservative drought. That was mislabeled -- 2011-2015 contains two
strong secular trends. The drought CORE is 2016-2019. This script makes that split
explicit.

Usage:
    python -m research.killed.diversified_trend.step4_drought_core
"""

import pandas as pd

from research.killed.diversified_trend.step4_backtest import (
    CAPITAL, VOL_TARGET,
    load_clean, run_backtest,
    periods_per_year, segment_metrics, max_dd_window,
)

# Sub-windows (all strictly inside the existing IS series; nothing recomputed).
WINDOWS = [
    ("2011-07..2015-12  secular run (Abenomics + EZ bond bull)", "2011-07-01", "2015-12-31"),
    ("2016-01..2019-12  drought CORE (true drought)",            "2016-01-01", "2019-12-31"),
    ("2011-07..2019-12  full base case (gate window, ref)",      "2011-07-01", "2019-12-31"),
]


def main():
    print("#" * 78)
    print("# Step 3 IS backtest -- DROUGHT-CORE SUB-WINDOW CONFIRMATION (pre-kill)")
    print("# Sub-window of the existing IS run. No new test, no sweep, no OOS.")
    print("#" * 78 + "\n")

    data = load_clean()
    bt = run_backtest(data)
    net = bt["res"]["net_pnl"]
    # One ppy for the whole IS series (same convention as step4_backtest headline),
    # so per-window Sharpes are directly comparable to the 0.481 gate number.
    ppy = periods_per_year(bt["res"].index)
    print(f"\n  Empirical periods/year (full IS, used for all windows) = {ppy:.1f}")
    print(f"  Base capital = ${CAPITAL:,.0f}; vol target = {VOL_TARGET*100:.0f}% annualized\n")

    print("=" * 78)
    print("BASKET NET PERFORMANCE BY SUB-WINDOW (same sizing/costs as the gate run)")
    print("=" * 78)
    print(f"  {'window':<52} {'n':>5} {'netShrp':>8} {'annRet':>8} "
          f"{'annVol':>8} {'maxDD':>8}")
    metrics = {}
    for label, a, b in WINDOWS:
        a, b = pd.Timestamp(a), pd.Timestamp(b)
        seg = net.loc[(net.index >= a) & (net.index <= b)]
        m = segment_metrics(seg, ppy)
        metrics[label] = (seg, m)
        print(f"  {label:<52} {m['n']:>5} {m['sharpe']:>8.3f} "
              f"{m['ann_ret']*100:>7.2f}% {m['ann_vol']*100:>7.2f}% "
              f"{m['max_dd_pct']*100:>7.2f}%")

    # Worst-drawdown window inside the drought core (dollars + dates on the record).
    print("\n" + "=" * 78)
    print("DROUGHT-CORE (2016-2019) WORST DRAWDOWN -- dollars and dates")
    print("=" * 78)
    dc_seg, dc_m = metrics[WINDOWS[1][0]]
    pk, tr, dd_pct, dd_usd = max_dd_window(dc_seg)
    print(f"  Drought-core net Sharpe   = {dc_m['sharpe']:.3f}")
    print(f"  Drought-core total net PnL= ${dc_seg.sum():,.0f} on ${CAPITAL:,.0f}")
    print(f"  Worst DD window: {pk.date()} (peak) -> {tr.date()} (trough)")
    print(f"  Worst DD depth:  {dd_pct*100:.2f}%  = ${dd_usd:,.0f}")

    # Decomposition statement (secular vs drought) at the portfolio level.
    print("\n" + "=" * 78)
    print("SECULAR vs DROUGHT DECOMPOSITION (portfolio level)")
    print("=" * 78)
    sec_seg, sec_m = metrics[WINDOWS[0][0]]
    full_seg, full_m = metrics[WINDOWS[2][0]]
    print(f"  2011-2015 secular run net Sharpe = {sec_m['sharpe']:.3f}  "
          f"(${sec_seg.sum():,.0f})")
    print(f"  2016-2019 drought core net Sharpe= {dc_m['sharpe']:.3f}  "
          f"(${dc_seg.sum():,.0f})")
    print(f"  2011-2019 full base case  Sharpe = {full_m['sharpe']:.3f}  "
          f"(${full_seg.sum():,.0f})   [the 0.481 gate window]")
    share = sec_seg.sum() / full_seg.sum() * 100 if full_seg.sum() != 0 else float("nan")
    print(f"\n  2011-2015 share of the full base-case net PnL = {share:.1f}%")
    print("  => The gate-window Sharpe is carried by the 2011-2015 secular block;")
    print("     the drought core (closest analogue to the 2024-2028 forward regime)")
    print("     is the number to weight when judging forward viability.")

    print("\n" + "#" * 78)
    print("# DROUGHT-CORE CONFIRMATION COMPLETE")
    print("#" * 78)


if __name__ == "__main__":
    main()
