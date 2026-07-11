"""
eda.py — Step 3 Signal EDA for the overnight-drift candidate.

Pure-pandas EDA, no engine, no SimBroker, no strategy module. IS ONLY
(2006-01-01 → 2018-12-31). OOS (2019+) is never loaded.

Produces outputs 1–5 per the workflow doc Step 3, with two corrections:
  - Tail study anchored on IS clusters (2008 GFC, 2011, Aug-2015, Feb/Dec-2018),
    NOT the OOS COVID cluster (which the doc mistakenly referenced).
  - Net-of-cost overlay on the decision comparison (overnight trades ~252
    round-trips/yr vs B&H ~0).

Run:  python -m research.killed.overnight_drift.eda
"""

from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from database.market_data import get_bars
from research.killed.spy_short_reversal.sensitivity import TB3MS_ANNUAL_PCT, _daily_rate

# ── Constants ────────────────────────────────────────────────────────────────

IS_START = "2006-01-01"
IS_END   = "2018-12-31"
SYMBOL   = "SPY"
PPY      = 252  # trading days per year

# The 2007-07-02 gap: both overnight returns spanning it are uncomputable.
GAP_DATE = pd.Timestamp("2007-07-02")

OUT_DIR = Path(__file__).resolve().parent
DIV_CSV = OUT_DIR / "spy_dividends.csv"


# ── Data loading ─────────────────────────────────────────────────────────────

def _load_is_bars() -> pd.DataFrame:
    """Load SPY daily TRADES bars, trimmed to IS only."""
    df = get_bars(SYMBOL, bar_size="1 day", what_to_show="TRADES")
    if df is None or df.empty:
        raise RuntimeError("No SPY TRADES bars in cache — run Step 2 first")
    df = df.loc[IS_START:IS_END].copy()
    if len(df) < 100:
        raise RuntimeError(f"Only {len(df)} IS bars — check date range")
    return df


def _load_dividends() -> pd.Series:
    """Load the dividend calendar, return a Series indexed by ex_date."""
    div = pd.read_csv(DIV_CSV, parse_dates=["ex_date"])
    div = div.set_index("ex_date")["amount"]
    return div.loc[IS_START:IS_END]


# ── Return computation ───────────────────────────────────────────────────────

def _compute_returns(bars: pd.DataFrame, divs: pd.Series) -> pd.DataFrame:
    """Compute overnight, intraday, and full-day returns.

    r_on(t) = (open(t) - close(t-1) + div(t)) / close(t-1)
    r_id(t) = (close(t) - open(t)) / open(t)
    full_day(t) = (1 + r_on(t)) * (1 + r_id(t)) - 1

    The dividend add-back: on ex-div mornings, add the dividend amount to the
    numerator of r_on because the open-drop by ~div is a mechanical artifact,
    not a tradeable loss.
    """
    df = pd.DataFrame(index=bars.index[1:])  # first bar has no prior close

    prev_close = bars["close"].iloc[:-1].values
    curr_open  = bars["open"].iloc[1:].values
    curr_close = bars["close"].iloc[1:].values

    div_amount = divs.reindex(df.index).fillna(0.0).values

    r_on = (curr_open - prev_close + div_amount) / prev_close
    r_id = (curr_close - curr_open) / curr_open

    df["r_on"] = r_on
    df["r_id"] = r_id
    df["r_full"] = (1.0 + r_on) * (1.0 + r_id) - 1.0
    df["div_amount"] = div_amount

    # Also compute r_on WITHOUT the dividend add-back for the sanity check.
    df["r_on_raw"] = (curr_open - prev_close) / prev_close

    return df


def _drop_gap_overnights(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the two overnight returns spanning the 2007-07-02 gap.

    The bar for 2007-07-02 is missing, so:
    - r_on for the first trading day AFTER the gap (uses close of 2007-07-02
      which doesn't exist)
    - r_on for 2007-07-02 itself (which isn't in the index — no bar)
    The effect: the bar *after* the gap has its overnight return computed from
    the close of the bar *before* the gap (2007-06-29), which spans 3+ days.
    That's the one we must drop.

    Also drop the bar before the gap if its intraday return would feed into a
    compounded full-day that spans the gap.

    In practice, the gap date itself is absent (no bar), and the next available
    date's overnight return is wrong. We find and drop that next date.
    """
    if GAP_DATE in df.index:
        df = df.drop(GAP_DATE)

    # Find the first date in df that is after the gap.
    after_gap = df.index[df.index > GAP_DATE]
    if len(after_gap) > 0:
        df = df.drop(after_gap[0])

    return df


# ── Cash / risk-free ─────────────────────────────────────────────────────────

def _add_rf_column(df: pd.DataFrame) -> pd.DataFrame:
    """Add a daily risk-free rate column based on the year of each date."""
    df["rf_daily"] = df.index.year.map(lambda y: _daily_rate(y))
    return df


# ── Output 1: Cumulative return decomposition ────────────────────────────────

def _plot_cumulative(df: pd.DataFrame) -> None:
    cum_on   = (1.0 + df["r_on"]).cumprod()
    cum_id   = (1.0 + df["r_id"]).cumprod()
    cum_full = (1.0 + df["r_full"]).cumprod()

    fig, ax = plt.subplots(figsize=(14, 7))
    ax.semilogy(df.index, cum_on,   label="Overnight only", linewidth=1.5)
    ax.semilogy(df.index, cum_id,   label="Intraday only",  linewidth=1.5)
    ax.semilogy(df.index, cum_full, label="Full day (B&H)",  linewidth=1.5,
                linestyle="--", alpha=0.7)

    ax.set_title("SPY Return Decomposition — IS 2006–2018 (log scale)")
    ax.set_ylabel("Growth of $1")
    ax.set_xlabel("")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
    fig.tight_layout()
    fig.savefig(OUT_DIR / "01_cumulative_decomposition.png", dpi=150)
    plt.close(fig)
    print("  → Saved 01_cumulative_decomposition.png")


# ── Output 2: r_on distribution ──────────────────────────────────────────────

def _print_distribution(df: pd.DataFrame) -> None:
    print("\n═══ Output 2: Overnight return (r_on) distribution ═══\n")

    # Full IS
    ron = df["r_on"]
    print(f"  Full IS ({df.index[0].date()} → {df.index[-1].date()})")
    print(f"    N        = {len(ron)}")
    print(f"    Mean     = {ron.mean():.5f}  ({ron.mean()*252*100:.2f}% ann.)")
    print(f"    Std      = {ron.std():.5f}  ({ron.std()*np.sqrt(252)*100:.2f}% ann.)")
    print(f"    Skew     = {ron.skew():.3f}")
    print(f"    Kurtosis = {ron.kurtosis():.3f}  (excess)")
    print(f"    Min      = {ron.min():.5f}")
    print(f"    Max      = {ron.max():.5f}")

    # By year
    print(f"\n  By year:")
    print(f"  {'Year':>6} {'N':>5} {'Mean bp':>9} {'Vol bp':>8} {'Skew':>7} {'Kurt':>7} {'Min bp':>9} {'Max bp':>9}")
    for year, grp in df.groupby(df.index.year):
        r = grp["r_on"]
        print(f"  {year:>6} {len(r):>5} {r.mean()*1e4:>9.2f} {r.std()*1e4:>8.2f} "
              f"{r.skew():>7.2f} {r.kurtosis():>7.2f} {r.min()*1e4:>9.1f} {r.max()*1e4:>9.1f}")

    # Worst 20 overnight losses
    print(f"\n  Worst 20 overnight losses (IS):")
    worst = ron.nsmallest(20)
    for i, (dt, val) in enumerate(worst.items(), 1):
        print(f"    {i:>2}. {dt.date()}  {val*100:>+7.3f}%  ({val*1e4:>+8.1f} bp)")


# ── Output 3: Sharpe comparison ──────────────────────────────────────────────

def _sharpe_excess(rets: pd.Series, rf_daily: pd.Series) -> float:
    """Annualized Sharpe, excess over cash."""
    excess = rets - rf_daily
    if excess.std() == 0:
        return 0.0
    return (excess.mean() / excess.std()) * np.sqrt(PPY)


def _print_sharpe_comparison(df: pd.DataFrame) -> None:
    print("\n═══ Output 3: Sharpe comparison — excess over cash ═══\n")

    # Full IS — gross
    sh_on   = _sharpe_excess(df["r_on"], df["rf_daily"])
    sh_id   = _sharpe_excess(df["r_id"], df["rf_daily"])
    sh_full = _sharpe_excess(df["r_full"], df["rf_daily"])

    print(f"  Full IS (GROSS):")
    print(f"    Overnight Sharpe  = {sh_on:.3f}")
    print(f"    Intraday Sharpe   = {sh_id:.3f}")
    print(f"    Full-day (B&H)    = {sh_full:.3f}")

    # Net-of-cost Sharpe for overnight
    # IBKR tiered: ~$0.0035/share, ~$550/share SPY → ~0.64 bp/side → ~1.3 bp RT
    # Bracket at 0.5, 1.0, 2.0 bp round-trip
    print(f"\n  Full IS (NET-OF-COST overnight, illustrative round-trip haircuts):")
    print(f"  {'RT cost bp':>12} {'Ann cost %':>11} {'Net Sharpe':>11}")
    for cost_bp in [0.5, 1.0, 2.0]:
        cost_per_day = cost_bp / 1e4
        net_on = df["r_on"] - cost_per_day
        sh_net = _sharpe_excess(net_on, df["rf_daily"])
        ann_cost = cost_per_day * PPY * 100
        print(f"  {cost_bp:>12.1f} {ann_cost:>11.2f} {sh_net:>11.3f}")

    # B&H has negligible cost (~0 RT/yr), so gross ≈ net. Show the delta.
    print(f"\n  B&H Sharpe (gross ≈ net) = {sh_full:.3f}")
    print(f"  → At 1 bp RT, overnight net-of-cost vs B&H delta = "
          f"{_sharpe_excess(df['r_on'] - 1.0/1e4, df['rf_daily']) - sh_full:+.3f}")

    # By year
    print(f"\n  By year (gross Sharpe, excess over cash):")
    print(f"  {'Year':>6} {'Overnight':>10} {'Intraday':>10} {'Full-day':>10} "
          f"{'ON−B&H':>8} {'ON(1bp net)':>12}")
    for year, grp in df.groupby(df.index.year):
        s_on   = _sharpe_excess(grp["r_on"],   grp["rf_daily"])
        s_id   = _sharpe_excess(grp["r_id"],   grp["rf_daily"])
        s_full = _sharpe_excess(grp["r_full"], grp["rf_daily"])
        net_on_1bp = grp["r_on"] - 1.0/1e4
        s_on_net = _sharpe_excess(net_on_1bp, grp["rf_daily"])
        print(f"  {year:>6} {s_on:>10.3f} {s_id:>10.3f} {s_full:>10.3f} "
              f"{s_on - s_full:>+8.3f} {s_on_net:>12.3f}")


# ── Output 4: Ex-div sanity (aggregate) ─────────────────────────────────────

def _print_exdiv_sanity(df: pd.DataFrame) -> None:
    print("\n═══ Output 4: Ex-dividend overnight return — aggregate sanity ═══\n")

    is_exdiv = df["div_amount"] > 0
    n_exdiv  = is_exdiv.sum()
    n_noexdiv = (~is_exdiv).sum()

    mean_on_exdiv_raw    = df.loc[is_exdiv, "r_on_raw"].mean()
    mean_on_noexdiv_raw  = df.loc[~is_exdiv, "r_on_raw"].mean()
    mean_on_exdiv_adj    = df.loc[is_exdiv, "r_on"].mean()
    mean_on_noexdiv_adj  = df.loc[~is_exdiv, "r_on"].mean()
    mean_div_yield       = df.loc[is_exdiv, "div_amount"].mean()

    # Compute mean div/price ratio on ex-div mornings (the expected artifact).
    exdiv_rows = df[is_exdiv]
    # We don't have prior close directly but can back it out:
    # r_on_raw = (open - prev_close) / prev_close → prev_close = open / (1 + r_on_raw)
    # div_yield ≈ div / prev_close
    # Actually, we have div_amount and can estimate: div/price ≈ div_amount / (typical SPY price)
    # Better: use the formula. r_on - r_on_raw = div / prev_close, exactly.
    mean_addback_bp = (exdiv_rows["r_on"] - exdiv_rows["r_on_raw"]).mean() * 1e4

    print(f"  Ex-div mornings in IS: {n_exdiv}")
    print(f"  Non-ex-div mornings:   {n_noexdiv}")
    print()
    print(f"  BEFORE add-back (raw overnight return):")
    print(f"    Ex-div mean   = {mean_on_exdiv_raw*1e4:>+8.2f} bp")
    print(f"    Non-ex-div    = {mean_on_noexdiv_raw*1e4:>+8.2f} bp")
    print(f"    Gap           = {(mean_on_exdiv_raw - mean_on_noexdiv_raw)*1e4:>+8.2f} bp")
    print()
    print(f"  AFTER add-back (dividend-corrected overnight return):")
    print(f"    Ex-div mean   = {mean_on_exdiv_adj*1e4:>+8.2f} bp")
    print(f"    Non-ex-div    = {mean_on_noexdiv_adj*1e4:>+8.2f} bp")
    print(f"    Gap           = {(mean_on_exdiv_adj - mean_on_noexdiv_adj)*1e4:>+8.2f} bp")
    print()
    print(f"  Mean add-back   = {mean_addback_bp:>+8.2f} bp  "
          f"(mean dividend ${mean_div_yield:.4f})")
    print(f"  Expected artifact (−div/price) removed: the gap should shrink toward 0.")


# ── Output 5: Tail study ─────────────────────────────────────────────────────

def _plot_tail_study(df: pd.DataFrame) -> None:
    print("\n═══ Output 5: Tail study — worst IS clusters ═══\n")

    # Worst 20 already printed in Output 2. Here: equity simulation through
    # worst IS cluster at candidate deployment fractions.

    starting_equity = 50_000.0
    fractions = [0.25, 0.50, 0.75, 1.00]

    # Identify IS stress clusters: 2008 GFC, 2011 Aug, 2015 Aug, 2018 Feb+Dec
    clusters = {
        "2008 GFC (Sep–Nov 2008)":  ("2008-09-01", "2008-11-30"),
        "2011 Aug selloff":         ("2011-07-25", "2011-08-31"),
        "2015 Aug flash":           ("2015-08-17", "2015-09-05"),
        "2018 Feb vol-shock":       ("2018-01-29", "2018-02-15"),
        "2018 Dec selloff":         ("2018-12-01", "2018-12-31"),
    }

    for name, (start, end) in clusters.items():
        cluster = df.loc[start:end]
        if len(cluster) == 0:
            continue

        print(f"  {name}  ({len(cluster)} days)")
        cum_on = (1.0 + cluster["r_on"]).cumprod()
        worst_cum = cum_on.min()
        print(f"    Worst cumulative overnight return: {(worst_cum - 1)*100:.2f}%")

        for frac in fractions:
            invested = starting_equity * frac
            cash = starting_equity * (1.0 - frac)
            equity_series = cash + invested * (1.0 + cluster["r_on"]).cumprod()
            min_eq = equity_series.min()
            print(f"    {frac*100:>5.0f}% deployed: min equity = ${min_eq:,.0f}  "
                  f"(DD = {(min_eq / starting_equity - 1)*100:>+.1f}%)"
                  f"{'  ← BREACH' if min_eq < 35_000 else ''}")
        print()

    # Worst rolling 5-session overnight loss across entire IS
    rolling_5 = df["r_on"].rolling(5).apply(lambda x: (1 + x).prod() - 1, raw=True)
    worst_5 = rolling_5.nsmallest(5)
    print(f"  Worst 5 rolling 5-session overnight losses (IS):")
    for i, (dt, val) in enumerate(worst_5.items(), 1):
        print(f"    {i}. ending {dt.date()}: {val*100:>+.2f}%")
        for frac in fractions:
            invested = starting_equity * frac
            cash = starting_equity * (1.0 - frac)
            min_eq = cash + invested * (1 + val)
            if min_eq < 35_000:
                print(f"       → {frac*100:.0f}% deployed: projected equity ${min_eq:,.0f} — BREACH")

    # Plot: equity curves through the 2008 GFC cluster at each fraction
    gfc = df.loc["2008-09-01":"2008-11-30"]
    if len(gfc) > 0:
        fig, ax = plt.subplots(figsize=(12, 6))
        for frac in fractions:
            invested = starting_equity * frac
            cash = starting_equity * (1.0 - frac)
            eq = cash + invested * (1.0 + gfc["r_on"]).cumprod()
            ax.plot(gfc.index, eq, label=f"{frac*100:.0f}% deployed", linewidth=1.5)

        ax.axhline(35_000, color="red", linestyle="--", alpha=0.7, label="$35k backstop")
        ax.axhline(25_000, color="darkred", linestyle=":", alpha=0.5, label="$25k PDT floor")
        ax.set_title("Equity Through 2008 GFC (Sep–Nov) — Overnight Strategy at Various Deployment Fractions")
        ax.set_ylabel("Portfolio equity ($)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("${x:,.0f}"))
        fig.tight_layout()
        fig.savefig(OUT_DIR / "05_tail_gfc_equity.png", dpi=150)
        plt.close(fig)
        print("\n  → Saved 05_tail_gfc_equity.png")


# ── Output 3 supplement: by-year Sharpe bar chart ────────────────────────────

def _plot_sharpe_by_year(df: pd.DataFrame) -> None:
    years = sorted(df.index.year.unique())
    sh_on_list, sh_id_list, sh_full_list, sh_on_net_list = [], [], [], []
    for year in years:
        grp = df[df.index.year == year]
        sh_on_list.append(_sharpe_excess(grp["r_on"], grp["rf_daily"]))
        sh_id_list.append(_sharpe_excess(grp["r_id"], grp["rf_daily"]))
        sh_full_list.append(_sharpe_excess(grp["r_full"], grp["rf_daily"]))
        net_1bp = grp["r_on"] - 1.0/1e4
        sh_on_net_list.append(_sharpe_excess(net_1bp, grp["rf_daily"]))

    x = np.arange(len(years))
    w = 0.22
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.bar(x - 1.5*w, sh_on_list,     w, label="Overnight (gross)")
    ax.bar(x - 0.5*w, sh_on_net_list,  w, label="Overnight (net 1bp RT)")
    ax.bar(x + 0.5*w, sh_id_list,     w, label="Intraday")
    ax.bar(x + 1.5*w, sh_full_list,   w, label="Full-day (B&H)")

    ax.set_xticks(x)
    ax.set_xticklabels(years)
    ax.set_ylabel("Annualized Sharpe (excess over cash)")
    ax.set_title("Sharpe by Year — Overnight vs Intraday vs B&H (IS 2006–2018)")
    ax.legend(loc="upper right")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "03_sharpe_by_year.png", dpi=150)
    plt.close(fig)
    print("  → Saved 03_sharpe_by_year.png")


# ── Output 2 supplement: r_on histogram ──────────────────────────────────────

def _plot_ron_distribution(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    ron_bp = df["r_on"] * 1e4
    ax.hist(ron_bp, bins=100, edgecolor="black", linewidth=0.3, alpha=0.7)
    ax.axvline(ron_bp.mean(), color="red", linestyle="--", label=f"Mean = {ron_bp.mean():.1f} bp")
    ax.set_xlabel("Overnight return (bp)")
    ax.set_ylabel("Frequency")
    ax.set_title("Distribution of Overnight Returns — IS 2006–2018")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "02_ron_distribution.png", dpi=150)
    plt.close(fig)
    print("  → Saved 02_ron_distribution.png")


# ── Decision point ───────────────────────────────────────────────────────────

def _decision_point(df: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("  DECISION POINT — Step 3 → Step 4 gate")
    print("=" * 72)

    # Full IS Sharpe comparison
    sh_on   = _sharpe_excess(df["r_on"], df["rf_daily"])
    sh_full = _sharpe_excess(df["r_full"], df["rf_daily"])

    # Net-of-cost overnight at 1 bp RT (the illustrative middle bracket)
    net_on_1bp = df["r_on"] - 1.0/1e4
    sh_on_net = _sharpe_excess(net_on_1bp, df["rf_daily"])

    print(f"\n  Full IS gross overnight Sharpe (excess/cash) = {sh_on:.3f}")
    print(f"  Full IS net overnight Sharpe (1 bp RT)        = {sh_on_net:.3f}")
    print(f"  Full IS B&H Sharpe (excess/cash)              = {sh_full:.3f}")
    print(f"  Delta (net overnight − B&H)                   = {sh_on_net - sh_full:+.3f}")

    # Decay check: does the edge persist in the recent-IS years (2015–2018)?
    recent = df.loc["2015-01-01":"2018-12-31"]
    sh_on_recent   = _sharpe_excess(recent["r_on"], recent["rf_daily"])
    sh_full_recent = _sharpe_excess(recent["r_full"], recent["rf_daily"])
    net_on_recent  = recent["r_on"] - 1.0/1e4
    sh_on_net_recent = _sharpe_excess(net_on_recent, recent["rf_daily"])

    print(f"\n  Recent-IS (2015–2018):")
    print(f"    Overnight gross Sharpe = {sh_on_recent:.3f}")
    print(f"    Overnight net 1bp      = {sh_on_net_recent:.3f}")
    print(f"    B&H Sharpe             = {sh_full_recent:.3f}")
    print(f"    Delta (net overnight − B&H) = {sh_on_net_recent - sh_full_recent:+.3f}")

    # Count years where overnight beats B&H (net-of-cost)
    years_beat = 0
    years_total = 0
    for year, grp in df.groupby(df.index.year):
        s_full = _sharpe_excess(grp["r_full"], grp["rf_daily"])
        net = grp["r_on"] - 1.0/1e4
        s_on_net = _sharpe_excess(net, grp["rf_daily"])
        years_total += 1
        if s_on_net > s_full:
            years_beat += 1

    print(f"\n  Years where net overnight beats B&H (Sharpe): {years_beat}/{years_total}")

    # Tail survivability at 50% deployment (conservative anchor)
    rolling_5 = df["r_on"].rolling(5).apply(lambda x: (1 + x).prod() - 1, raw=True)
    worst_5 = rolling_5.min()
    eq_50 = 50_000 * 0.5 * (1 + worst_5) + 50_000 * 0.5
    print(f"  Worst 5-session cluster at 50% deployment: equity = ${eq_50:,.0f}"
          f"{'  ← SURVIVES' if eq_50 >= 35_000 else '  ← BREACH'}")

    # Annualized return comparison
    n_years = len(df) / PPY
    ann_on = ((1 + df["r_on"]).prod()) ** (1/n_years) - 1
    ann_on_net = ((1 + net_on_1bp).prod()) ** (1/n_years) - 1
    ann_full = ((1 + df["r_full"]).prod()) ** (1/n_years) - 1
    print(f"\n  Annualized return (IS):")
    print(f"    Overnight gross = {ann_on*100:.2f}%")
    print(f"    Overnight net   = {ann_on_net*100:.2f}%  (1 bp RT haircut)")
    print(f"    Full-day (B&H)  = {ann_full*100:.2f}%")

    print(f"\n  Verdict: {'PROCEED to Step 4' if sh_on_net > sh_full and years_beat > years_total // 2 else 'REVIEW — see notes above'}")
    print("=" * 72)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Overnight Drift — Step 3 Signal EDA (IS only: 2006–2018)")
    print("=" * 60)

    # Load
    bars = _load_is_bars()
    divs = _load_dividends()
    print(f"  Loaded {len(bars)} IS bars ({bars.index[0].date()} → {bars.index[-1].date()})")
    print(f"  Loaded {len(divs)} IS ex-dividend dates")

    # Compute returns
    df = _compute_returns(bars, divs)
    df = _drop_gap_overnights(df)
    df = _add_rf_column(df)
    print(f"  Computed returns for {len(df)} trading days (after gap-date drop)")

    # Output 1: Cumulative decomposition
    print("\n═══ Output 1: Cumulative return decomposition ═══")
    _plot_cumulative(df)

    # Output 2: r_on distribution
    _print_distribution(df)
    _plot_ron_distribution(df)

    # Output 3: Sharpe comparison
    _print_sharpe_comparison(df)
    _plot_sharpe_by_year(df)

    # Output 4: Ex-div sanity
    _print_exdiv_sanity(df)

    # Output 5: Tail study
    _plot_tail_study(df)

    # Decision point
    _decision_point(df)


if __name__ == "__main__":
    main()
