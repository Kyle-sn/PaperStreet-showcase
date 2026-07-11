"""
Step 4 — In-sample portfolio backtest (diversified TSMOM).

Vectorized research backtest, IS only (2011-07-01 to 2022-12-31). OOS quarantined.
Per RESEARCH_WORKFLOW_diversified_trend.md. NO production-engine changes.

This is the IS backtest ONLY:
  - NOT parameter sensitivity (later step)
  - NOT precise cost modeling (later step) — uses a rough flat bps haircut
  - NOT OOS (quarantined)

PRE-COMMITTED GATE (locked before viewing results):
  Base case = 2011-2019 segment. Proceed to next step iff 2011-2019 NET Sharpe >= 0.30.
  If 2011-2019 nets < ~0.10 after costs: STOP. 2020/2022 are qualitative stress checks,
  NOT part of the numeric gate.

DESIGN (all locked upstream):
  - Signal:  signal[T] = sign(trailing 252-day return on ratio-adjusted signal series,
             through settlement T).
  - Lag:     position[T+1] = signal[T]; return earned = position[T+1] * (settle[T+2]-settle[T+1]).
             => booked pnl at index d uses position determined with info through d-2.
  - Sizing:  10% annualized portfolio vol target; per-instrument inverse-vol weights from
             60-day PIT realized $-vol (THROUGH T ONLY); equal-risk / zero-correlation
             ex-ante scaling; 25% per-instrument variance-share cap; vol-estimate floor
             (trailing 1yr 20th percentile) to prevent inverse-vol explosion.
  - Capital: $5,000,000 (locked, Open Decision #4). Integer-rounded contracts.
  - Costs:   rough flat per-round-trip bps of notional, charged on each sign flip:
             ES,ZN=1bp; 6E,6J,6A=2bp; CL,GC=2bp; HG=4bp.

DATA NOTE: the CME Globex Sunday-evening pseudo-settlements (~51/yr, sub-tick noise)
are collapsed into the next trading day in the canonical build layer
(build_continuous.collapse_sundays), so futures_research.db is already Sunday-free and
this backtest, the parameter sweep, and OOS all read an identical series. load_clean()
asserts no Sundays remain rather than re-cleaning inline. (Earlier revisions of this
file did the collapse here; that duplicate path has been removed.)

Usage:
    python -m research.killed.diversified_trend.step4_backtest
"""

import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

RESEARCH_DB = Path(__file__).resolve().parent.parent.parent / "data" / "futures_research.db"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

ROOTS = ["ES", "ZN", "6E", "6J", "6A", "CL", "GC", "HG"]
IS_START = pd.Timestamp("2011-07-01")
IS_END = pd.Timestamp("2022-12-31")

# --- Locked design parameters ---
CAPITAL = 5_000_000.0
VOL_TARGET = 0.10          # annualized portfolio vol target
LOOKBACK = 252             # trading-day momentum lookback
VOL_WINDOW = 60            # trading-day PIT realized-vol window
FLOOR_WINDOW = 252         # trailing window for the vol-estimate floor (1yr)
FLOOR_PCTILE = 20          # percentile for the vol-estimate floor
RISK_CAP = 0.25            # max single-instrument share of portfolio variance

# Rough per-round-trip cost haircut, bps of notional (Step-4 placeholder only).
COST_BPS = {
    "ES": 1.0, "ZN": 1.0,
    "6E": 2.0, "6J": 2.0, "6A": 2.0,
    "CL": 2.0, "GC": 2.0, "HG": 4.0,
}

MULTIPLIERS = {
    "ES": 50.0, "ZN": 1000.0, "6E": 125_000.0, "6J": 12_500_000.0,
    "6A": 100_000.0, "CL": 1_000.0, "GC": 100.0, "HG": 25_000.0,
}


# ---------------------------------------------------------------------------
# Load + clean
# ---------------------------------------------------------------------------

def load_clean() -> dict[str, pd.DataFrame]:
    """Load the WARMUP+IS continuous series per root from the research DB.

    The Sunday pseudo-settlement collapse now lives in the canonical build layer
    (build_continuous.collapse_sundays) and is baked into futures_research.db, so
    every consumer (this IS backtest, the parameter sweep, and OOS) reads an
    identical Sunday-free series. We assert that invariant here rather than
    re-cleaning inline.

    Returns per-root DataFrame indexed by trade_date with columns:
      signal_price, pnl_daily_usd, raw_settle, is_roll.
    """
    conn = sqlite3.connect(RESEARCH_DB)
    out = {}
    for root in ROOTS:
        df = pd.read_sql(
            """
            SELECT trade_date, signal_price, pnl_daily_usd, raw_settle, is_roll
            FROM futures_continuous
            WHERE root = ? AND sample IN ('WARMUP', 'IS')
            ORDER BY trade_date
            """,
            conn, params=(root,),
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        n_sun = int((df["trade_date"].dt.dayofweek == 6).sum())
        assert n_sun == 0, (
            f"{root}: {n_sun} Sunday rows still present in futures_research.db. "
            f"Run `python -m research.killed.diversified_trend.build_continuous --recollapse`."
        )
        out[root] = df.set_index("trade_date")
    conn.close()

    print("Loaded Sunday-free continuous series (collapse done in build layer):")
    for root in ROOTS:
        print(f"  {root}: {len(out[root])} rows")
    return out


# ---------------------------------------------------------------------------
# Signal, vol, position sizing  (all PIT, lagged identically)
# ---------------------------------------------------------------------------

def compute_signal(price: pd.Series) -> pd.Series:
    """sign(trailing 252-day return). Exact-zero -> carry prior signal forward."""
    trailing = price / price.shift(LOOKBACK) - 1.0
    sig = np.sign(trailing)
    sig = sig.replace(0, np.nan).ffill()
    return sig


def compute_dollar_vol(pnl: pd.Series) -> pd.Series:
    """60-day PIT realized $-vol per 1 contract, through T (std of settle-to-settle $ PnL)."""
    return pnl.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std()


def apply_floor(dvol: pd.Series) -> pd.Series:
    """Floor the vol estimate at its trailing 1yr 20th percentile (PIT)."""
    floor = dvol.rolling(FLOOR_WINDOW, min_periods=VOL_WINDOW).quantile(FLOOR_PCTILE / 100.0)
    return np.maximum(dvol, floor)


def build_positions(data: dict[str, pd.DataFrame], master: pd.DatetimeIndex):
    """Build the signed integer-contract position matrix (info-through-T convention).

    Returns:
      positions:   DataFrame [date x root], signed integer contracts determined using
                   info THROUGH that date (i.e. this is position[T] in signal-date space;
                   it becomes live at T+1 and earns the T+1->T+2 move).
      pnl_pc:      DataFrame [date x root], $ PnL per 1 contract on that date (settle move).
      signals:     DataFrame [date x root], +/-1.
      dvol_floored:DataFrame [date x root], floored per-contract $-vol used for sizing.
      notional_pc: DataFrame [date x root], $ notional per 1 contract (|raw_settle|*mult).
      cap_binds:   int, number of (date,instrument) cells where the 25% cap bound.
    """
    signals, dvol_f, pnl_pc, notional_pc = {}, {}, {}, {}
    for root, df in data.items():
        price = df["signal_price"]
        signals[root] = compute_signal(price).reindex(master).ffill()
        dvol_f[root] = apply_floor(compute_dollar_vol(df["pnl_daily_usd"])).reindex(master).ffill()
        pnl_pc[root] = df["pnl_daily_usd"].reindex(master).fillna(0.0)
        notional_pc[root] = (df["raw_settle"].abs() * MULTIPLIERS[root]).reindex(master).ffill()

    signals = pd.DataFrame(signals)[ROOTS]
    dvol_f = pd.DataFrame(dvol_f)[ROOTS]
    pnl_pc = pd.DataFrame(pnl_pc)[ROOTS]
    notional_pc = pd.DataFrame(notional_pc)[ROOTS]

    # Target dollar vols (annualized -> daily via empirical periods/yr computed later;
    # for sizing we use sqrt(252) as the canonical daily<->annual bridge for the *target*,
    # consistent with the locked 10% annualized design).
    target_port_daily = VOL_TARGET * CAPITAL / np.sqrt(252.0)
    target_per_instr_daily = target_port_daily / np.sqrt(len(ROOTS))

    raw_contracts = target_per_instr_daily / dvol_f          # magnitude
    n_contracts = np.round(raw_contracts)                    # integer
    positions = (n_contracts * signals)                      # signed integer

    # --- 25% per-instrument variance-share cap (backstop; expected to rarely bind) ---
    cap_binds = 0
    pos_arr = positions.values.copy()
    dv_arr = dvol_f.values
    for t in range(pos_arr.shape[0]):
        n = pos_arr[t]
        dv = dv_arr[t]
        if np.any(np.isnan(n)) or np.any(np.isnan(dv)):
            continue
        var = (n * dv) ** 2
        tot = var.sum()
        if tot <= 0:
            continue
        share = var / tot
        over = share > RISK_CAP
        if over.any():
            for j in np.where(over)[0]:
                others_var = tot - var[j]
                # cap var_j so var_j / (var_j + others_var) = RISK_CAP
                max_var_j = RISK_CAP / (1.0 - RISK_CAP) * others_var
                max_n = np.floor(np.sqrt(max_var_j) / dv[j])
                pos_arr[t, j] = np.sign(n[j]) * min(abs(n[j]), max_n)
                cap_binds += 1
    positions = pd.DataFrame(pos_arr, index=positions.index, columns=ROOTS)

    return positions, pnl_pc, signals, dvol_f, notional_pc, cap_binds


# ---------------------------------------------------------------------------
# Backtest assembly
# ---------------------------------------------------------------------------

def run_backtest(data: dict[str, pd.DataFrame]):
    # Master trading-day index = union of all roots' clean dates (WARMUP+IS).
    master = data[ROOTS[0]].index
    for root in ROOTS[1:]:
        master = master.union(data[root].index)
    master = master.sort_values()

    positions, pnl_pc, signals, dvol_f, notional_pc, cap_binds = build_positions(data, master)

    # --- Lag: booked pnl[d] = position[d-2] * pnl_per_contract[d] (per design) ---
    pos_live = positions.shift(2)            # info-through-(d-2) position earns the d move
    gross_contrib = pos_live * pnl_pc        # per-instrument $ pnl booked at date d
    gross_pnl = gross_contrib.sum(axis=1)

    # --- Costs: charge on each sign flip of the *live* position direction ---
    # Flip = signal change vs prior day (Step 3 turnover def); first signal = init, not a flip.
    live_sign = np.sign(pos_live).replace(0, np.nan).ffill()
    flips = (live_sign != live_sign.shift(1)) & live_sign.shift(1).notna()
    # cost per flip = bps * notional of the position being put on (lagged like the position)
    notional_live = (pos_live.abs() * notional_pc)   # $ notional held per instrument
    cost_contrib = pd.DataFrame(0.0, index=master, columns=ROOTS)
    for root in ROOTS:
        c = COST_BPS[root] / 10_000.0
        cost_contrib[root] = flips[root].astype(float) * notional_live[root] * c
    cost_pnl = cost_contrib.sum(axis=1)
    net_pnl = gross_pnl - cost_pnl

    # Trim to IS for all reporting.
    is_mask = (master >= IS_START) & (master <= IS_END)
    res = pd.DataFrame({
        "gross_pnl": gross_pnl, "cost": cost_pnl, "net_pnl": net_pnl,
    }, index=master)[is_mask]
    gross_contrib = gross_contrib[is_mask]
    cost_contrib = cost_contrib[is_mask]
    flips = flips[is_mask]
    signals_is = signals[is_mask]
    positions_is = positions[is_mask]

    return {
        "res": res, "gross_contrib": gross_contrib, "cost_contrib": cost_contrib,
        "flips": flips, "signals": signals_is, "positions": positions_is,
        "pnl_pc": pnl_pc[is_mask], "pos_live": pos_live[is_mask],
        "cap_binds": cap_binds, "master_is": master[is_mask],
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def periods_per_year(idx: pd.DatetimeIndex) -> float:
    years = (idx[-1] - idx[0]).days / 365.25
    return len(idx) / years


def segment_metrics(pnl: pd.Series, ppy: float) -> dict:
    """Sharpe / ann return / ann vol / max DD on a $-PnL series against CAPITAL."""
    ret = pnl / CAPITAL
    mu, sd = ret.mean(), ret.std(ddof=1)
    sharpe = (mu / sd * np.sqrt(ppy)) if sd > 0 else np.nan
    ann_ret = mu * ppy
    ann_vol = sd * np.sqrt(ppy)
    equity = CAPITAL + pnl.cumsum()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd_pct = dd.min()
    max_dd_usd = (equity - peak).min()
    return {
        "sharpe": sharpe, "ann_ret": ann_ret, "ann_vol": ann_vol,
        "max_dd_pct": max_dd_pct, "max_dd_usd": max_dd_usd,
        "n": len(pnl), "total_pnl": pnl.sum(),
    }


def print_segment(name: str, m: dict):
    print(f"\n  {name}  (n={m['n']} days)")
    print(f"    Sharpe (net):       {m['sharpe']:.3f}")
    print(f"    Ann. return:        {m['ann_ret']*100:6.2f}%   (${m['total_pnl']:,.0f} total)")
    print(f"    Ann. vol:           {m['ann_vol']*100:6.2f}%")
    print(f"    Max drawdown:       {m['max_dd_pct']*100:6.2f}%   (${m['max_dd_usd']:,.0f})")


def max_dd_window(pnl: pd.Series):
    """Return (peak_date, trough_date, dd_pct, dd_usd) for the worst drawdown."""
    equity = CAPITAL + pnl.cumsum()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    trough = dd.idxmin()
    peak_date = equity.loc[:trough].idxmax()
    return peak_date, trough, dd.min(), (equity - peak).min()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 72)
    print("Step 4 — In-Sample Portfolio Backtest (Diversified TSMOM)")
    print("=" * 72)

    data = load_clean()
    bt = run_backtest(data)
    res = bt["res"]
    ppy = periods_per_year(res.index)

    gross = res["gross_pnl"]
    net = res["net_pnl"]

    # === GATE FIRST ===
    base_mask = (res.index >= "2011-07-01") & (res.index <= "2019-12-31")
    m_base_net = segment_metrics(net[base_mask], ppy)
    m_base_gross = segment_metrics(gross[base_mask], ppy)

    print("\n" + "=" * 72)
    print("PRE-COMMITTED GATE — 2011-2019 base case, NET of rough cost haircut")
    print("=" * 72)
    gate_sharpe = m_base_net["sharpe"]
    verdict = "PASS" if gate_sharpe >= 0.30 else (
        "STOP (near-zero/negative)" if gate_sharpe < 0.10 else "FAIL (between 0.10 and 0.30)")
    print(f"\n  2011-2019 NET Sharpe = {gate_sharpe:.3f}   vs threshold 0.30   -> {verdict}")
    print(f"  (gross Sharpe = {m_base_gross['sharpe']:.3f}; "
          f"cost drag = {m_base_gross['sharpe'] - gate_sharpe:.3f} Sharpe)")
    print(f"\n  Empirical periods/year (clean series) = {ppy:.1f}")
    print(f"  Cap bound on {bt['cap_binds']} (date,instrument) cells over full WARMUP+IS.")

    # === FULL-IS equity curve summary (not the headline) ===
    print("\n" + "=" * 72)
    print("FULL-IS context (2011-07-01 to 2022-12-31) — NOT the headline number")
    print("=" * 72)
    m_full_net = segment_metrics(net, ppy)
    m_full_gross = segment_metrics(gross, ppy)
    print(f"  Full-IS NET Sharpe   = {m_full_net['sharpe']:.3f}  "
          f"(gross {m_full_gross['sharpe']:.3f})")
    print(f"  Full-IS net total PnL= ${net.sum():,.0f} on ${CAPITAL:,.0f}")
    print(f"  Full-IS net ann ret  = {m_full_net['ann_ret']*100:.2f}%, "
          f"ann vol {m_full_net['ann_vol']*100:.2f}%, "
          f"maxDD {m_full_net['max_dd_pct']*100:.2f}%")

    # === REGIME SEGMENTS (reported separately) ===
    print("\n" + "=" * 72)
    print("REGIME-SEGMENTED PERFORMANCE (separate, not pooled) — NET")
    print("=" * 72)
    print_segment("2011-2019 base case (GATE)", m_base_net)
    for yr in (2020, 2022):
        mask = res.index.year == yr
        print_segment(f"{yr} stress check (behavior, not pass/fail)",
                      segment_metrics(net[mask], ppy))

    # === REALIZED VOL vs TARGET, BY YEAR ===
    print("\n" + "=" * 72)
    print("REALIZED PORTFOLIO VOL vs 10% TARGET, BY YEAR (net)")
    print("=" * 72)
    print(f"  {'year':>6} {'realized vol':>14} {'vs target':>12}")
    for yr, grp in net.groupby(net.index.year):
        rv = (grp / CAPITAL).std(ddof=1) * np.sqrt(ppy)
        print(f"  {yr:>6} {rv*100:>12.2f}% {rv/VOL_TARGET:>11.2f}x")

    # === PER-INSTRUMENT CONTRIBUTION (return + variance) ===
    print("\n" + "=" * 72)
    print("PER-INSTRUMENT CONTRIBUTION (full IS)")
    print("=" * 72)
    gc = bt["gross_contrib"]
    # cost per instrument
    cc = bt["cost_contrib"].sum()
    net_by_instr = gc.sum() - cc
    total_net = net_by_instr.sum()
    # Variance (risk) contribution via Euler: RC_i = Cov(r_i, p)/Var(p), sums to 1.
    p = gc.sum(axis=1)
    var_p = p.var(ddof=1)
    rc = {root: gc[root].cov(p) / var_p for root in ROOTS}
    print(f"  {'root':>5} {'net PnL $':>14} {'ret share':>10} {'risk share':>11} "
          f"{'cost $':>12}")
    for root in ROOTS:
        print(f"  {root:>5} {net_by_instr[root]:>14,.0f} "
              f"{net_by_instr[root]/total_net*100:>9.1f}% {rc[root]*100:>10.1f}% "
              f"{cc[root]:>12,.0f}")
    print(f"  {'TOTAL':>5} {total_net:>14,.0f} {100.0:>9.1f}% "
          f"{sum(rc.values())*100:>10.1f}% {cc.sum():>12,.0f}")
    dom = max(rc, key=rc.get)
    if rc[dom] > 0.40:
        print(f"  ** RED FLAG: {dom} carries {rc[dom]*100:.0f}% of portfolio variance (>40%).")

    # === ZN/CL ALIGNMENT CHECK ===
    print("\n" + "=" * 72)
    print("ZN / CL SIGNAL-ALIGNMENT CHECK (Step 3 flagged signal corr -0.60)")
    print("=" * 72)
    sig = bt["signals"]
    agree = (sig["ZN"] == sig["CL"]).mean()
    contrib_corr = gc["ZN"].corr(gc["CL"])
    sig_corr = sig["ZN"].corr(sig["CL"])
    print(f"  IS signal correlation ZN/CL (clean series): {sig_corr:.3f}  "
          f"(Step 3 raw-series reported -0.60)")
    print(f"  Fraction of IS days ZN & CL share the same sign: {agree*100:.1f}%")
    print(f"  Correlation of ZN vs CL daily $ PnL contributions: {contrib_corr:.3f}")

    # === WORST DRAWDOWN: dollars, and signal alignment during it ===
    print("\n" + "=" * 72)
    print("WORST IS DRAWDOWN (net) — depth, dollars, and signal concentration")
    print("=" * 72)
    pk, tr, dd_pct, dd_usd = max_dd_window(net)
    print(f"  Window: {pk.date()} (peak) -> {tr.date()} (trough)")
    print(f"  Depth:  {dd_pct*100:.2f}%   =  ${dd_usd:,.0f}  on ${CAPITAL:,.0f} base")
    # signal alignment during the drawdown window
    win = (sig.index >= pk) & (sig.index <= tr)
    sig_win = sig[win]
    if len(sig_win):
        # dominant sign per day = sum of signs; count days with >=6/8 aligned
        net_dir = sig_win.sum(axis=1)
        aligned_days = (net_dir.abs() >= 6).mean()
        print(f"  During the drawdown, {aligned_days*100:.0f}% of days had >=6/8 instrument "
              f"signals pointing the same way (diversification thinning).")
        zncl_win = (sig_win["ZN"] == sig_win["CL"]).mean()
        print(f"  ZN & CL shared sign on {zncl_win*100:.0f}% of drawdown days.")

    # === DRAWDOWN AGAINST $50k (prompt deliverable #5) — flagged inconsistency ===
    print("\n" + "=" * 72)
    print("DELIVERABLE #5 NOTE — '$50k account / $25k PDT floor'")
    print("=" * 72)
    print("  The prompt asks to confirm the worst IS DD against a $50k account with a")
    print("  $25k PDT floor. This conflicts with the LOCKED, rebased research design")
    print("  (Open Decision #4: capital = $5,000,000; PDT N/A for futures). The conflict")
    print("  is not cosmetic — it is degenerate:")
    print(f"    - At $50k / 10% vol target, target per-instrument daily $-vol is")
    print(f"      ~${VOL_TARGET*50_000/np.sqrt(252)/np.sqrt(8):,.0f}; one full-size contract's daily $-vol is")
    print(f"      hundreds-to-thousands of dollars, so EVERY position rounds to 0 contracts.")
    print(f"      The $50k book cannot hold the basket at all — which is precisely the")
    print(f"      documented reason capital was rebased to $5M.")
    print(f"    - Vol targeting makes Sharpe / %-return / %-DD scale-invariant, so the gate")
    print(f"      and all percentage metrics above are identical at any viable capital base.")
    print(f"  Worst IS drawdown, reported at the ACTUAL locked base ($5M): "
          f"{dd_pct*100:.2f}% = ${dd_usd:,.0f}.")
    print(f"  (At $50k the same {dd_pct*100:.2f}% would be ${dd_pct*50_000:,.0f}, but the book")
    print(f"   is un-tradeable at $50k, so this figure is presented only to honor the")
    print(f"   request; the $5M figure is the operative one.)")

    # === SIGNAL-LAG HAND-CHECK (auditable, one date) ===
    print("\n" + "=" * 72)
    print("SIGNAL-TO-POSITION LAG HAND-CHECK (ES, one date)")
    print("=" * 72)
    hand_check(data, bt)

    # === EQUITY CURVE PLOT ===
    plot_equity(res, ppy)

    print("\n" + "=" * 72)
    print("Step 4 COMPLETE")
    print("=" * 72)


def hand_check(data: dict[str, pd.DataFrame], bt: dict):
    """Print signal[T], position[T+1], settle[T+1], settle[T+2], earned return for ES."""
    root = "ES"
    df = data[root]
    mult = MULTIPLIERS[root]
    positions = bt["positions"]      # IS, signal-date convention (position[T])
    pnl_pc = bt["pnl_pc"]

    # pick a non-roll T well inside IS so T+1, T+2 exist and no roll contaminates
    es_idx = positions.index
    # choose a date and verify no roll in [T+1, T+2]
    chosen = None
    for cand in es_idx[260:400]:
        loc = df.index.get_indexer([cand])[0]
        if loc < 1 or loc + 2 >= len(df):
            continue
        T = df.index[loc]
        Tp1 = df.index[loc + 1]
        Tp2 = df.index[loc + 2]
        if df.loc[Tp1, "is_roll"] or df.loc[Tp2, "is_roll"]:
            continue
        chosen = (loc, T, Tp1, Tp2)
        break
    loc, T, Tp1, Tp2 = chosen

    sig_T = compute_signal(df["signal_price"]).loc[T]
    n_T = positions.loc[T, root] if T in positions.index else np.nan  # signed contracts (position[T])
    settle_Tp1 = df.loc[Tp1, "raw_settle"]
    settle_Tp2 = df.loc[Tp2, "raw_settle"]
    move = settle_Tp2 - settle_Tp1
    earned = n_T * move * mult
    booked = bt["pos_live"].loc[Tp2, root] * pnl_pc.loc[Tp2, root] if Tp2 in pnl_pc.index else np.nan

    print(f"  T        = {T.date()}   signal[T]        = {sig_T:+.0f}")
    print(f"  position determined w/ info through T (=position[T+1]) = {n_T:+.0f} contracts")
    print(f"  settle[T+1] ({Tp1.date()}) = {settle_Tp1:.2f}")
    print(f"  settle[T+2] ({Tp2.date()}) = {settle_Tp2:.2f}")
    print(f"  move (settle[T+2]-settle[T+1]) = {move:+.2f} pts")
    print(f"  earned = position * move * mult = {n_T:+.0f} * {move:+.2f} * {mult:.0f} "
          f"= ${earned:,.2f}")
    print(f"  cross-check vs booked pnl_live[T+2] = ${booked:,.2f}  "
          f"({'MATCH' if abs(earned-booked) < 1e-6 else 'MISMATCH'})")


def plot_equity(res: pd.DataFrame, ppy: float):
    eq_gross = CAPITAL + res["gross_pnl"].cumsum()
    eq_net = CAPITAL + res["net_pnl"].cumsum()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(eq_gross.index, eq_gross / 1e6, label="Gross", color="gray", lw=0.9)
    ax1.plot(eq_net.index, eq_net / 1e6, label="Net (rough costs)", color="black", lw=1.0)
    ax1.axhline(CAPITAL / 1e6, color="red", ls=":", lw=0.7)
    for yr in (2020, 2022):
        ax1.axvspan(pd.Timestamp(f"{yr}-01-01"), pd.Timestamp(f"{yr}-12-31"),
                    color="orange", alpha=0.08)
    ax1.set_ylabel("Equity ($M)")
    ax1.set_title("Diversified TSMOM — IS equity curve ($5M base, 10% vol target)")
    ax1.legend(loc="upper left")

    peak = eq_net.cummax()
    dd = (eq_net - peak) / peak * 100
    ax2.fill_between(dd.index, dd.values, 0, color="red", alpha=0.4)
    ax2.set_ylabel("Net DD (%)")
    fig.tight_layout()
    path = OUTPUT_DIR / "step4_equity_curve.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"\n  Saved equity curve: {path.name}")


if __name__ == "__main__":
    main()
