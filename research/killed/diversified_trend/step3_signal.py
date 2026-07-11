"""
Step 3 — Signal construction (diversified trend).

Signal: sign(trailing 252-day return on ratio-adjusted signal series).
Per instrument, IS only (2011-07-01 to 2022-12-31). OOS quarantined.

Deliverables:
  1. Per-instrument signal series (±1)
  2. Turnover (sign-flip counts per calendar year)
  3. Signal correlation vs return correlation (8×8)
  4. Sanity-check plots: price with long/short shading (ES, GC, CL)

OUT OF SCOPE: position sizes, PnL, cumulative returns, Sharpe, drawdown.

Usage:
    python -m research.killed.diversified_trend.step3_signal
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
IS_START = "2011-07-01"
IS_END = "2022-12-31"
LOOKBACK = 252


def load_data() -> dict[str, pd.DataFrame]:
    """Load warmup + IS signal_price series per root from SQLite."""
    conn = sqlite3.connect(RESEARCH_DB)
    data = {}
    for root in ROOTS:
        df = pd.read_sql(
            """
            SELECT trade_date, signal_price
            FROM futures_continuous
            WHERE root = ? AND sample IN ('WARMUP', 'IS')
            ORDER BY trade_date
            """,
            conn,
            params=(root,),
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        data[root] = df
    conn.close()
    return data


def compute_signal(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute sign(trailing 252-day return) per instrument. Returns IS-only."""
    signals = {}
    first_valid_dates = {}

    for root, df in data.items():
        price = df["signal_price"]
        trailing_ret = price / price.shift(LOOKBACK) - 1.0
        sig = np.sign(trailing_ret)

        # Handle exact-zero returns: carry forward prior signal
        zero_mask = sig == 0
        if zero_mask.any():
            sig = sig.replace(0, np.nan).ffill()
            n_zeros = zero_mask.sum()
            print(f"  {root}: {n_zeros} exact-zero trailing returns, filled forward")

        # If the very first valid signal is NaN (no prior to fill), default +1
        first_valid_idx = sig.first_valid_index()
        if first_valid_idx is not None and pd.isna(sig.iloc[0]):
            pass  # trailing NaNs before first valid are fine, they're warmup

        first_valid_dates[root] = first_valid_idx

        # Trim to IS window
        is_mask = (df.index >= IS_START) & (df.index <= IS_END)
        sig_is = sig[is_mask].copy()

        # Verify no NaN in IS window
        n_nan = sig_is.isna().sum()
        if n_nan > 0:
            print(f"  WARNING {root}: {n_nan} NaN signals in IS window")

        signals[root] = sig_is

    # Report first valid dates
    print("\nFirst valid signal dates (252-day lookback):")
    for root, fvd in first_valid_dates.items():
        clears = fvd <= pd.Timestamp(IS_START) if fvd is not None else False
        print(f"  {root}: {fvd.date() if fvd else 'N/A'} — clears IS start: {clears}")

    signal_df = pd.DataFrame(signals)
    signal_df.index.name = "trade_date"
    return signal_df


def turnover_analysis(signal_df: pd.DataFrame) -> pd.DataFrame:
    """Count sign flips per instrument per calendar year."""
    signal_df = signal_df.copy()
    signal_df["year"] = signal_df.index.year

    records = []
    for root in ROOTS:
        sig = signal_df[root]
        flips = (sig != sig.shift(1)) & sig.shift(1).notna()
        signal_df[f"{root}_flip"] = flips.astype(int)

        for year, grp in signal_df.groupby("year"):
            n_flips = grp[f"{root}_flip"].sum()
            records.append({"root": root, "year": year, "flips": n_flips})

    turnover = pd.DataFrame(records)
    pivot = turnover.pivot(index="year", columns="root", values="flips")
    pivot = pivot[ROOTS]
    return pivot


def correlation_analysis(
    data: dict[str, pd.DataFrame],
    signal_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute signal correlation and return correlation matrices (IS only)."""
    # Signal correlation (on the ±1 series)
    sig_corr = signal_df[ROOTS].corr()

    # Return correlation (on daily log returns of signal_price)
    returns = {}
    for root, df in data.items():
        price = df["signal_price"]
        is_mask = (df.index >= IS_START) & (df.index <= IS_END)
        ret = np.log(price / price.shift(1))
        returns[root] = ret[is_mask]

    ret_df = pd.DataFrame(returns)
    ret_corr = ret_df[ROOTS].corr()

    return sig_corr, ret_corr


def plot_signal_overlay(
    data: dict[str, pd.DataFrame],
    signal_df: pd.DataFrame,
    roots_to_plot: list[str],
) -> None:
    """Plot price with long/short shading for selected instruments."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for root in roots_to_plot:
        df = data[root]
        is_mask = (df.index >= IS_START) & (df.index <= IS_END)
        price = df.loc[is_mask, "signal_price"]
        sig = signal_df[root]

        # Align
        common_idx = price.index.intersection(sig.index)
        price = price.loc[common_idx]
        sig = sig.loc[common_idx]

        fig, ax = plt.subplots(figsize=(14, 5))
        ax.plot(price.index, price.values, color="black", linewidth=0.7, label="Signal price")

        # Shade long (green) and short (red) regions
        long_mask = sig == 1
        short_mask = sig == -1

        y_min, y_max = price.min(), price.max()
        margin = (y_max - y_min) * 0.05
        y_lo, y_hi = y_min - margin, y_max + margin

        # Block-shade: find contiguous regions
        for mask, color, alpha, label in [
            (long_mask, "green", 0.15, "Long"),
            (short_mask, "red", 0.15, "Short"),
        ]:
            vals = mask.values.astype(int)
            diffs = np.diff(vals, prepend=0, append=0)
            starts = np.where(diffs == 1)[0]
            ends = np.where(diffs == -1)[0]
            for s_idx, e_idx in zip(starts, ends):
                s_date = common_idx[s_idx]
                e_date = common_idx[min(e_idx - 1, len(common_idx) - 1)]
                ax.axvspan(s_date, e_date, color=color, alpha=alpha,
                           label=label if s_idx == starts[0] else None)

        ax.set_title(f"{root} — Ratio-Adjusted Price with Trend Signal (IS: {IS_START} to {IS_END})")
        ax.set_ylabel("Signal Price")
        ax.legend(loc="upper left")
        ax.set_xlim(common_idx[0], common_idx[-1])
        ax.set_ylim(y_lo, y_hi)
        fig.tight_layout()
        fig.savefig(OUTPUT_DIR / f"step3_{root}_signal_overlay.png", dpi=150)
        plt.close(fig)
        print(f"  Saved: step3_{root}_signal_overlay.png")


def main():
    print("=" * 60)
    print("Step 3 — Signal Construction (Diversified Trend)")
    print("=" * 60)

    # --- Load data ---
    print("\nLoading data...")
    data = load_data()
    for root, df in data.items():
        print(f"  {root}: {len(df)} rows, {df.index.min().date()} to {df.index.max().date()}")

    # --- Compute signal ---
    print("\nComputing signals (sign of 252-day trailing return)...")
    signal_df = compute_signal(data)
    print(f"\nSignal DataFrame: {signal_df.shape[0]} dates × {signal_df.shape[1]} instruments")
    print(f"IS window: {signal_df.index.min().date()} to {signal_df.index.max().date()}")

    # --- Turnover ---
    print("\n" + "=" * 60)
    print("TURNOVER ANALYSIS")
    print("=" * 60)
    turnover = turnover_analysis(signal_df)
    print("\nSign flips per instrument per year:")
    print(turnover.to_string())

    print("\nSummary (flips/year across instruments):")
    flat = turnover.values.flatten()
    flat = flat[~np.isnan(flat)]
    print(f"  Min:    {flat.min():.0f}")
    print(f"  Median: {np.median(flat):.0f}")
    print(f"  Max:    {flat.max():.0f}")
    print(f"  Mean:   {flat.mean():.1f}")

    print("\nPer-instrument median flips/year:")
    for root in ROOTS:
        med = turnover[root].median()
        print(f"  {root}: {med:.0f} flips/yr (= {med:.0f} round-trips/yr)")

    # Flag outliers (>2× median of other instruments)
    medians = {r: turnover[r].median() for r in ROOTS}
    overall_med = np.median(list(medians.values()))
    for root, m in medians.items():
        if m > 2 * overall_med:
            print(f"  ** OUTLIER: {root} ({m:.0f} flips) > 2× overall median ({overall_med:.0f})")

    # --- Correlation matrices ---
    print("\n" + "=" * 60)
    print("CORRELATION ANALYSIS")
    print("=" * 60)
    sig_corr, ret_corr = correlation_analysis(data, signal_df)

    print("\nSignal correlation (±1 series, Pearson):")
    print(sig_corr.round(3).to_string())

    print("\nReturn correlation (daily log returns, Pearson):")
    print(ret_corr.round(3).to_string())

    # Flag pairs where signal corr notably exceeds return corr
    print("\nPairs where |signal_corr| > |return_corr| + 0.10:")
    flagged = []
    for i, r1 in enumerate(ROOTS):
        for j, r2 in enumerate(ROOTS):
            if j <= i:
                continue
            sc = abs(sig_corr.loc[r1, r2])
            rc = abs(ret_corr.loc[r1, r2])
            if sc > rc + 0.10:
                flagged.append((r1, r2, sig_corr.loc[r1, r2], ret_corr.loc[r1, r2]))
                print(f"  {r1}/{r2}: signal={sig_corr.loc[r1, r2]:.3f}, "
                      f"return={ret_corr.loc[r1, r2]:.3f}, "
                      f"gap={sc - rc:.3f}")
    if not flagged:
        print("  (none)")

    # --- 2020 stale-position check ---
    print("\n" + "=" * 60)
    print("2020 REGIME CHECK — STALE POSITIONS")
    print("=" * 60)
    for root in ROOTS:
        sig = signal_df[root]
        mask_2020 = (sig.index >= "2020-01-01") & (sig.index <= "2020-12-31")
        sig_2020 = sig[mask_2020]
        if sig_2020.empty:
            continue
        flips_2020 = ((sig_2020 != sig_2020.shift(1)) & sig_2020.shift(1).notna()).sum()

        # Longest constant-signal stretch in 2020
        changes = sig_2020 != sig_2020.shift(1)
        groups = changes.cumsum()
        max_streak = groups.value_counts().max()

        print(f"  {root}: {flips_2020} flips in 2020, "
              f"longest same-signal streak: {max_streak} trading days "
              f"({max_streak / 252 * 12:.1f} months)")

    # --- Plots ---
    print("\n" + "=" * 60)
    print("SANITY-CHECK PLOTS")
    print("=" * 60)
    plot_signal_overlay(data, signal_df, ["ES", "GC", "CL"])

    print("\n" + "=" * 60)
    print("Step 3 COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
