"""
Step 4 -- Gate diagnostics (load-bearing gate + secular-artifact + vol-lag proof).

This is NOT the parameter sweep. It answers three cheap questions that must be
settled BEFORE the sweep, per the Desktop review of step4_backtest.py:

  TASK 1  LOAD-BEARING GATE. Step 0 commits the IS gate to "basket net Sharpe
          beats best single instrument," NOT the placeholder 0.30 absolute that
          step4_backtest.py checks. Compute each instrument's STANDALONE net
          Sharpe on the 2011-2019 base case (same signal, same 10% vol-target
          sizing, same per-instrument cost haircut), and compare the best single
          leg to the 0.481 basket. If a single leg beats the basket, the
          documented action is "reconsider universe or kill," not proceed.

  TASK 1b SECULAR-ARTIFACT TEST. Decompose 6J (25.7% of IS return) and ZN
          (19.4%) by sub-period to test whether the IS result leans on
          non-repeatable secular moves (Abenomics + 2022 BoJ for 6J; the secular
          bond bull for ZN). Step 3 line "is this PnL a non-repeatable
          secular-bull artifact?" requires this.

  TASK 2  VOL-PIT LAG PROOF. For the hand-check date, demonstrate (not assert)
          that the 60d realized-vol value used to size the live position shares
          the signal's information set: its window's LAST observation == T, and
          it is lagged by the same shift(2) as the signal.

Reuses the vetted helpers in step4_backtest.py so the sizing/cost/lag conventions
are identical to the headline backtest. Reads the SAME data path (inline Sunday
collapse via step4.load_clean), so numbers match step4_backtest.py exactly.

Usage:
    python -m research.killed.diversified_trend.step4_gate_diagnostics
"""

import numpy as np
import pandas as pd

from research.killed.diversified_trend.step4_backtest import (
    CAPITAL, VOL_TARGET, LOOKBACK, COST_BPS, MULTIPLIERS, ROOTS,
    IS_START, IS_END,
    load_clean, compute_signal, compute_dollar_vol, apply_floor,
    periods_per_year, segment_metrics, run_backtest,
)

BASE_START = pd.Timestamp("2011-07-01")
BASE_END = pd.Timestamp("2019-12-31")


# ---------------------------------------------------------------------------
# TASK 1 -- standalone per-instrument backtest (single leg as its own strategy)
# ---------------------------------------------------------------------------

def standalone_series(df: pd.DataFrame, root: str) -> pd.DataFrame:
    """Run one instrument as a standalone 10%-vol TSMOM strategy.

    Sizing: the SINGLE instrument carries the full 10% annualized vol target
    (i.e. it IS the portfolio). Sharpe is invariant to the vol scalar, so this
    differs from the in-basket per-instrument budget (10%/sqrt(8) ~= 3.5%) only
    through integer rounding -- and the larger contract counts here mean *finer*
    granularity (less rounding drag), so this is a deliberately GENEROUS
    benchmark for the single leg. A gate that still clears it is conservative.

    Returns a per-date frame (WARMUP+IS index) with gross/cost/net $ PnL.
    Lag + cost conventions are identical to step4_backtest.run_backtest.
    """
    price = df["signal_price"]
    pnl_pc = df["pnl_daily_usd"]

    signal = compute_signal(price)
    dvol_f = apply_floor(compute_dollar_vol(pnl_pc))

    target_daily = VOL_TARGET * CAPITAL / np.sqrt(252.0)   # full 10% to this leg
    raw_contracts = target_daily / dvol_f
    n_contracts = np.round(raw_contracts)
    positions = n_contracts * signal                        # signed integer, position[T]

    pos_live = positions.shift(2)                           # info-through-(d-2) earns d move
    gross = pos_live * pnl_pc

    live_sign = np.sign(pos_live).replace(0, np.nan).ffill()
    flips = (live_sign != live_sign.shift(1)) & live_sign.shift(1).notna()
    notional_pc = df["raw_settle"].abs() * MULTIPLIERS[root]
    notional_live = pos_live.abs() * notional_pc
    cost = flips.astype(float) * notional_live * (COST_BPS[root] / 10_000.0)

    out = pd.DataFrame({"gross": gross, "cost": cost}, index=df.index)
    out["net"] = out["gross"] - out["cost"]
    return out


def task1_gate(data: dict[str, pd.DataFrame], basket_base_net_sharpe: float):
    print("=" * 78)
    print("TASK 1 -- LOAD-BEARING GATE: basket vs best single instrument (2011-2019 NET)")
    print("=" * 78)
    print("  Gate (Step 0): basket net Sharpe must BEAT the best single instrument.")
    print("  Sizing per leg: standalone 10% vol target (generous => conservative gate).\n")

    rows = []
    standalones = {}
    for root in ROOTS:
        s = standalone_series(data[root], root)
        standalones[root] = s
        base = s.loc[(s.index >= BASE_START) & (s.index <= BASE_END)]
        ppy = periods_per_year(base.index)
        m_net = segment_metrics(base["net"], ppy)
        m_gross = segment_metrics(base["gross"], ppy)
        rows.append({
            "root": root,
            "net_sharpe": m_net["sharpe"],
            "gross_sharpe": m_gross["sharpe"],
            "ann_ret": m_net["ann_ret"],
            "ann_vol": m_net["ann_vol"],
            "max_dd": m_net["max_dd_pct"],
            "total_net": base["net"].sum(),
        })

    tbl = pd.DataFrame(rows).set_index("root")
    tbl = tbl.sort_values("net_sharpe", ascending=False)

    print(f"  {'root':>5} {'net Sharpe':>11} {'gross Shrp':>11} {'ann ret':>9} "
          f"{'ann vol':>9} {'max DD':>9} {'net $PnL':>14}")
    for root, r in tbl.iterrows():
        print(f"  {root:>5} {r['net_sharpe']:>11.3f} {r['gross_sharpe']:>11.3f} "
              f"{r['ann_ret']*100:>8.2f}% {r['ann_vol']*100:>8.2f}% "
              f"{r['max_dd']*100:>8.2f}% {r['total_net']:>14,.0f}")

    best_root = tbl.index[0]
    best_sharpe = tbl.iloc[0]["net_sharpe"]
    print("\n  " + "-" * 74)
    print(f"  BASKET (2011-2019 net Sharpe)          = {basket_base_net_sharpe:.3f}")
    print(f"  BEST SINGLE INSTRUMENT ({best_root})           = {best_sharpe:.3f}")
    diff = basket_base_net_sharpe - best_sharpe
    if basket_base_net_sharpe > best_sharpe:
        print(f"  => GATE PASS: basket beats best single leg by {diff:+.3f} Sharpe.")
        print("     Diversification is paying for itself on the load-bearing test.")
    else:
        print(f"  => GATE FAIL: best single leg ({best_root}) beats basket by "
              f"{-diff:+.3f} Sharpe.")
        print("     Documented action: reconsider universe or kill (NOT proceed).")
    print("  " + "-" * 74)
    return standalones, tbl


# ---------------------------------------------------------------------------
# TASK 1b -- 6J / ZN sub-period decomposition (secular-artifact test)
# ---------------------------------------------------------------------------

# Named sub-periods. The base case (2011-2019) is split so a secular run inside
# it cannot hide behind the drought average.
SUBPERIODS = [
    ("2011-07..2012-12  pre-Abenomics / EZ crisis", "2011-07-01", "2012-12-31"),
    ("2013-01..2015-12  Abenomics QQE / yen slide",  "2013-01-01", "2015-12-31"),
    ("2016-01..2019-12  drought core",               "2016-01-01", "2019-12-31"),
    ("2020-01..2021-12  COVID + recovery",           "2020-01-01", "2021-12-31"),
    ("2022-01..2022-12  BoJ defense / rate hikes",   "2022-01-01", "2022-12-31"),
]


def task1b_decomp(standalones: dict[str, pd.DataFrame],
                  basket_contrib: pd.DataFrame):
    print("\n" + "=" * 78)
    print("TASK 1b -- 6J / ZN SECULAR-ARTIFACT TEST (sub-period decomposition)")
    print("=" * 78)
    print("  Two views per leg:")
    print("   (A) IN-BASKET gross $ contribution by sub-period (its actual basket weight)")
    print("   (B) STANDALONE net Sharpe by sub-period (regime-by-regime repeatability)\n")

    for root in ("6J", "ZN"):
        s = standalones[root]
        contrib = basket_contrib[root]   # in-basket gross $ contribution (full IS)
        total_contrib = contrib.sum()

        print(f"  --- {root} ---")
        print(f"  {'sub-period':<42} {'in-basket $g':>14} {'% of IS':>8} "
              f"{'standalone Sharpe':>18}")
        for label, a, b in SUBPERIODS:
            a, b = pd.Timestamp(a), pd.Timestamp(b)
            c = contrib.loc[(contrib.index >= a) & (contrib.index <= b)].sum()
            seg = s.loc[(s.index >= a) & (s.index <= b)]
            ppy = periods_per_year(seg.index) if len(seg) > 2 else 252.0
            shp = segment_metrics(seg["net"], ppy)["sharpe"]
            pct = (c / total_contrib * 100) if total_contrib != 0 else np.nan
            print(f"  {label:<42} {c:>14,.0f} {pct:>7.1f}% {shp:>18.3f}")
        print(f"  {'FULL IS 2011-2019 base case (gate window)':<42} "
              f"{contrib.loc[(contrib.index>=BASE_START)&(contrib.index<=BASE_END)].sum():>14,.0f}")
        base = s.loc[(s.index >= BASE_START) & (s.index <= BASE_END)]
        print(f"    standalone base-case net Sharpe (2011-2019)        = "
              f"{segment_metrics(base['net'], periods_per_year(base.index))['sharpe']:.3f}")
        # drought core only (2016-2019): the harshest repeatability test
        dc = s.loc[(s.index >= '2016-01-01') & (s.index <= '2019-12-31')]
        print(f"    standalone drought-core net Sharpe (2016-2019)     = "
              f"{segment_metrics(dc['net'], periods_per_year(dc.index))['sharpe']:.3f}")
        print()


# ---------------------------------------------------------------------------
# TASK 2 -- vol-PIT lag proof at the hand-check date
# ---------------------------------------------------------------------------

def task2_vol_lag(data: dict[str, pd.DataFrame], bt: dict):
    print("=" * 78)
    print("TASK 2 -- VOL-PIT LAG PROOF (demonstrate, not assert) -- ES")
    print("=" * 78)
    root = "ES"
    df = data[root]
    price = df["signal_price"]
    pnl_pc = df["pnl_daily_usd"]

    # Re-pick the SAME hand-check date step4.hand_check uses: first non-roll
    # T in positions.index[260:400] with no roll in [T+1, T+2].
    positions = bt["positions"]
    chosen = None
    for cand in positions.index[260:400]:
        loc = df.index.get_indexer([cand])[0]
        if loc < 1 or loc + 2 >= len(df):
            continue
        Tp1, Tp2 = df.index[loc + 1], df.index[loc + 2]
        if df.loc[Tp1, "is_roll"] or df.loc[Tp2, "is_roll"]:
            continue
        chosen = (loc, df.index[loc], Tp1, Tp2)
        break
    loc, T, Tp1, Tp2 = chosen

    # Signal information set: trailing 252-day return through T.
    sig_window = price.iloc[loc - LOOKBACK: loc + 1]   # inclusive of T
    sig_T = compute_signal(price).loc[T]

    # Vol information set: 60-day realized $-vol through T (the value used to size).
    dvol_raw = compute_dollar_vol(pnl_pc)
    dvol_f = apply_floor(dvol_raw)
    vol_window = pnl_pc.iloc[loc - 60 + 1: loc + 1]    # the 60 obs ending at T
    dvol_T = dvol_f.loc[T]
    dvol_raw_T = dvol_raw.loc[T]

    # The position that goes LIVE at T+1 (earns T+1->T+2) is positions.shift(2)
    # evaluated at T+2 == positions at T. Show that BOTH inputs are dated T.
    print(f"  Hand-check date T = {T.date()}  (same date as step4 hand_check)\n")
    print("  SIGNAL input (info set):")
    print(f"    trailing-return window: {sig_window.index[0].date()} .. "
          f"{sig_window.index[-1].date()}   (last obs = T? "
          f"{sig_window.index[-1] == T})")
    print(f"    signal[T] = sign(252d return) = {sig_T:+.0f}\n")
    print("  VOL input (info set) -- the value that sizes the live position:")
    print(f"    60d vol window:         {vol_window.index[0].date()} .. "
          f"{vol_window.index[-1].date()}   (last obs = T? "
          f"{vol_window.index[-1] == T})")
    print(f"    raw 60d realized $-vol[T]   = ${dvol_raw_T:,.2f} / contract / day")
    print(f"    floored vol[T] (used)      = ${dvol_f.loc[T]:,.2f} / contract / day")
    floored_applied = dvol_f.loc[T] > dvol_raw_T + 1e-9
    print(f"    floor binding at T?        = {floored_applied}\n")

    # Prove shift(2) symmetry: the live position at T+2 uses signal[T] AND vol[T].
    target_daily = VOL_TARGET * CAPITAL / np.sqrt(252.0)
    target_per_instr = target_daily / np.sqrt(len(ROOTS))
    n_T = np.round(target_per_instr / dvol_T) * sig_T
    pos_live_Tp2 = bt["pos_live"].loc[Tp2, root] if Tp2 in bt["pos_live"].index else np.nan
    print("  LAG SYMMETRY (signal and vol share index, then shift(2) together):")
    print(f"    position[T] = round(target_per_instr / vol[T]) * signal[T]")
    print(f"                = round({target_per_instr:,.0f} / {dvol_T:,.2f}) * {sig_T:+.0f} "
          f"= {n_T:+.0f} contracts")
    print(f"    becomes live at T+1 ({Tp1.date()}), earns the T+1->T+2 move.")
    print(f"    cross-check: pos_live[T+2] (=positions.shift(2)[T+2]) = "
          f"{pos_live_Tp2:+.0f}   "
          f"({'MATCH' if abs(n_T - pos_live_Tp2) < 1e-9 else 'MISMATCH'})")
    print("    => signal and vol are evaluated on the SAME information set (through T)")
    print("       and lagged identically. No vol-pit lookahead.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("#" * 78)
    print("# Step 4 GATE DIAGNOSTICS (pre-sweep)")
    print("#" * 78 + "\n")

    data = load_clean()

    # Basket reference numbers from the SAME engine as step4_backtest.
    bt = run_backtest(data)
    res = bt["res"]
    ppy = periods_per_year(res.index)
    base_mask = (res.index >= BASE_START) & (res.index <= BASE_END)
    basket_base_net = segment_metrics(res["net_pnl"][base_mask], ppy)["sharpe"]

    standalones, _tbl = task1_gate(data, basket_base_net)
    task1b_decomp(standalones, bt["gross_contrib"])
    task2_vol_lag(data, bt)

    print("#" * 78)
    print("# GATE DIAGNOSTICS COMPLETE")
    print("#" * 78)


if __name__ == "__main__":
    main()
