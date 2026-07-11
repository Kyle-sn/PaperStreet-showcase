"""
Quality checks for QQQ 5-min bar data after Step 2 loading.

Validates:
  1. No gaps within RTH on non-holiday trading days
  2. Half-day sessions identified, labeled, and exportable for filtering
  3. DST transitions produce consistent open times
  4. Spot-check daily OHLC (derived from 5-min bars) vs Yahoo Finance
  5. Cross-source parity: IBKR vs external CSV over the overlap window

Usage:
  python -m research.killed.intraday_conditional.quality_checks           # all checks
  python -m research.killed.intraday_conditional.quality_checks --parity  # cross-source only

The checks read from the existing market_data_bars cache — run fetch_5min_bars
first.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from database import initialize_db
from database import market_data as _mdb
from utils.log_config import setup_logger

logger = setup_logger(__name__)

import functools
print = functools.partial(print, flush=True)

SYMBOL = "QQQ"
BAR_SIZE = "5 mins"
WHAT_TO_SHOW = "TRADES"

# 5-min bars per full RTH session (9:30–16:00 ET = 78 bars)
FULL_DAY_BARS = 78
# 5-min bars per half-day session (9:30–13:00 ET = 42 bars)
HALF_DAY_BARS = 42

# Output directory for check artifacts
_OUT_DIR = Path(__file__).resolve().parent


def _load_bars() -> pd.DataFrame:
    df = _mdb.get_bars(SYMBOL, bar_size=BAR_SIZE, what_to_show=WHAT_TO_SHOW)
    if df is None or df.empty:
        raise ValueError(
            f"No {SYMBOL} {BAR_SIZE} bars in DB. Run fetch_5min_bars first.")
    # Normalize mixed-tz index (CST -06:00 / CDT -05:00) to a single tz so
    # .date, .hour, etc. work as vectorized accessors on a proper DatetimeIndex.
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/Chicago")
    elif df.index.tz is None:
        df.index = df.index.tz_localize("America/Chicago")
    return df


def _bars_per_day(df: pd.DataFrame) -> pd.DataFrame:
    """Group bars by calendar date, return bar count + first/last timestamp per day."""
    dates = df.index.date
    grouped = df.groupby(dates)
    result = pd.DataFrame({
        "bar_count": grouped.size(),
        "first_bar": grouped.apply(lambda g: g.index.min()),
        "last_bar": grouped.apply(lambda g: g.index.max()),
    })
    result.index.name = "date"
    return result


# ---------------------------------------------------------------------------
# Check 1: RTH gap detection
# ---------------------------------------------------------------------------

def check_rth_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Find trading days with unexpected bar counts (not 78 full or 42 half-day).

    Returns a DataFrame of anomalous days with their bar counts.
    """
    print("\n=== Check 1: RTH gap detection ===\n")
    daily = _bars_per_day(df)
    anomalous = daily[~daily["bar_count"].isin([FULL_DAY_BARS, HALF_DAY_BARS])]

    if anomalous.empty:
        print(f"  PASS — all {len(daily)} trading days have {FULL_DAY_BARS} "
              f"(full) or {HALF_DAY_BARS} (half-day) bars")
    else:
        print(f"  WARN — {len(anomalous)} day(s) with unexpected bar count:")
        for date, row in anomalous.iterrows():
            print(f"    {date}: {row['bar_count']} bars  "
                  f"(first={row['first_bar']}, last={row['last_bar']})")

    total_full = (daily["bar_count"] == FULL_DAY_BARS).sum()
    total_half = (daily["bar_count"] == HALF_DAY_BARS).sum()
    print(f"\n  Summary: {total_full} full days, {total_half} half days, "
          f"{len(anomalous)} anomalous, {len(daily)} total")

    return anomalous


# ---------------------------------------------------------------------------
# Check 2: Half-day identification
# ---------------------------------------------------------------------------

def identify_half_days(df: pd.DataFrame) -> list[str]:
    """Return sorted list of half-day dates (ISO strings) and save to JSON."""
    print("\n=== Check 2: Half-day identification ===\n")
    daily = _bars_per_day(df)
    half_days = daily[daily["bar_count"] == HALF_DAY_BARS]

    dates = sorted(str(d) for d in half_days.index)

    if dates:
        print(f"  Found {len(dates)} half-day sessions:")
        for d in dates:
            row = half_days.loc[pd.Timestamp(d).date()]
            print(f"    {d}  (last bar: {row['last_bar']})")
    else:
        print("  No half-day sessions found.")

    out_path = _OUT_DIR / "half_days.json"
    with open(out_path, "w") as f:
        json.dump({"symbol": SYMBOL, "bar_size": BAR_SIZE, "half_days": dates}, f,
                  indent=2)
    print(f"\n  Saved to {out_path}")

    return dates


# ---------------------------------------------------------------------------
# Check 3: DST transition consistency
# ---------------------------------------------------------------------------

def check_dst_transitions(df: pd.DataFrame) -> bool:
    """Verify the first bar of each day has a consistent time-of-day.

    In US/Central (TWS default), the open bar should always be 08:30 CT
    regardless of DST. If the timestamps are naive ISO (no tz offset), the
    time should be constant.  If they carry tz offsets, the wall-clock time
    should still be constant after conversion.
    """
    print("\n=== Check 3: DST transition check ===\n")
    daily = _bars_per_day(df)

    first_times = daily["first_bar"].apply(
        lambda ts: ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)[-8:-3]
    )
    unique_times = first_times.unique()

    if len(unique_times) == 1:
        print(f"  PASS — first bar consistently at {unique_times[0]} "
              f"across all {len(daily)} days")
        return True

    print(f"  WARN — {len(unique_times)} distinct first-bar times:")
    for t in sorted(unique_times):
        count = (first_times == t).sum()
        print(f"    {t}: {count} days")

    # Check known DST transition dates
    dst_dates = []
    for year in range(df.index.min().year, df.index.max().year + 1):
        # Spring forward (2nd Sunday in March)
        mar1 = pd.Timestamp(f"{year}-03-01")
        spring = mar1 + pd.offsets.Week(weekday=6) + pd.offsets.Week(weekday=6)
        spring = spring - pd.Timedelta(days=6) + pd.Timedelta(weeks=1)
        # Simpler: use pandas
        for d in pd.date_range(f"{year}-03-08", f"{year}-03-14"):
            if d.dayofweek == 6:
                dst_dates.append(d.date())
                break
        for d in pd.date_range(f"{year}-11-01", f"{year}-11-07"):
            if d.dayofweek == 6:
                dst_dates.append(d.date())
                break

    # Check the Monday after each DST transition
    for d in dst_dates:
        monday = d + pd.Timedelta(days=1)
        if monday in first_times.index:
            print(f"    DST transition {d} -> Monday {monday}: "
                  f"first bar at {first_times[monday]}")

    return len(unique_times) == 1


# ---------------------------------------------------------------------------
# Check 4: Spot-check vs Yahoo Finance
# ---------------------------------------------------------------------------

def spot_check_vs_yahoo(df: pd.DataFrame, n_days: int = 10) -> pd.DataFrame | None:
    """Compare daily OHLC derived from 5-min bars against Yahoo Finance.

    Picks n_days spread across the date range. Returns a comparison DataFrame
    or None if yfinance is not installed.
    """
    print(f"\n=== Check 4: Spot-check vs Yahoo ({n_days} days) ===\n")

    try:
        import yfinance as yf
    except ImportError:
        print("  SKIP — yfinance not installed (pip install yfinance)")
        return None

    # Derive daily OHLC from 5-min bars
    dates = sorted(set(df.index.date))
    step = max(1, len(dates) // n_days)
    sample_dates = dates[::step][:n_days]

    our_daily = []
    for d in sample_dates:
        day_bars = df[df.index.date == d]
        our_daily.append({
            "date": d,
            "our_open": day_bars["open"].iloc[0],
            "our_high": day_bars["high"].max(),
            "our_low": day_bars["low"].min(),
            "our_close": day_bars["close"].iloc[-1],
        })
    ours = pd.DataFrame(our_daily).set_index("date")

    # Fetch Yahoo daily data for the same range
    start = str(min(sample_dates))
    end = str(max(sample_dates) + pd.Timedelta(days=1))
    yahoo = yf.download(SYMBOL, start=start, end=end, progress=False)
    if yahoo.empty:
        print("  SKIP — Yahoo returned no data")
        return None

    # Handle multi-level columns from yfinance
    if isinstance(yahoo.columns, pd.MultiIndex):
        yahoo.columns = yahoo.columns.get_level_values(0)
    yahoo.index = yahoo.index.date

    # Compare
    results = []
    for d in sample_dates:
        if d not in yahoo.index:
            continue
        y = yahoo.loc[d]
        o = ours.loc[d]
        results.append({
            "date": d,
            "open_diff_bps": abs(o["our_open"] - y["Open"]) / y["Open"] * 10000,
            "high_diff_bps": abs(o["our_high"] - y["High"]) / y["High"] * 10000,
            "low_diff_bps": abs(o["our_low"] - y["Low"]) / y["Low"] * 10000,
            "close_diff_bps": abs(o["our_close"] - y["Close"]) / y["Close"] * 10000,
        })

    if not results:
        print("  SKIP — no overlapping dates with Yahoo")
        return None

    comp = pd.DataFrame(results).set_index("date")
    max_diff = comp.max().max()
    mean_diff = comp.mean().mean()

    if max_diff < 10:
        print(f"  PASS — max diff {max_diff:.1f} bps, mean {mean_diff:.1f} bps")
    else:
        print(f"  WARN — max diff {max_diff:.1f} bps (>10 bps), "
              f"mean {mean_diff:.1f} bps")
    print(comp.to_string())

    return comp


# ---------------------------------------------------------------------------
# Check 5: Cross-source parity (IBKR vs external)
# ---------------------------------------------------------------------------

def cross_source_parity(
    ibkr_start: str, ibkr_end: str,
    external_csv: str | None = None,
) -> pd.DataFrame | None:
    """Compare IBKR bars vs external-source bars over the overlap window.

    If external_csv is None, tries to compare two what_to_show variants or
    skips. External bars must already be loaded into the DB (via load_csv).
    """
    print(f"\n=== Check 5: Cross-source parity ({ibkr_start} -> {ibkr_end}) ===\n")

    if external_csv is None:
        print("  SKIP — no external CSV provided. Run after loading external data.")
        return None

    # Load external CSV directly for comparison (don't rely on DB — it may
    # have been merged already)
    ext = pd.read_csv(external_csv)
    ext.columns = [c.strip().lower() for c in ext.columns]
    if "date" in ext.columns and "time" in ext.columns:
        ext["datetime"] = ext["date"].astype(str) + " " + ext["time"].astype(str)
        ext = ext.drop(columns=["date", "time"])
    ext["datetime"] = pd.to_datetime(ext["datetime"])
    ext = ext.set_index("datetime").sort_index()
    ext = ext[ibkr_start:ibkr_end]

    # Load IBKR bars from DB
    ibkr = _mdb.get_bars(SYMBOL, bar_size=BAR_SIZE, what_to_show=WHAT_TO_SHOW)
    if ibkr is None:
        print("  SKIP — no IBKR bars in DB")
        return None
    ibkr = ibkr[ibkr_start:ibkr_end]

    if ibkr.empty or ext.empty:
        print(f"  SKIP — IBKR has {len(ibkr)} bars, external has {len(ext)} bars "
              f"in overlap window")
        return None

    # Align on timestamp
    merged = ibkr[["open", "high", "low", "close"]].join(
        ext[["open", "high", "low", "close"]],
        lsuffix="_ibkr", rsuffix="_ext", how="inner",
    )

    if merged.empty:
        print("  WARN — no overlapping timestamps (timezone mismatch?)")
        return None

    # Compute differences in bps
    for col in ["open", "high", "low", "close"]:
        merged[f"{col}_diff_bps"] = (
            abs(merged[f"{col}_ibkr"] - merged[f"{col}_ext"])
            / merged[f"{col}_ibkr"] * 10000
        )

    diff_cols = [c for c in merged.columns if c.endswith("_diff_bps")]
    stats = merged[diff_cols].describe()

    max_diff = merged[diff_cols].max().max()
    mean_diff = merged[diff_cols].mean().mean()

    if max_diff < 5:
        print(f"  PASS — max diff {max_diff:.2f} bps, mean {mean_diff:.2f} bps "
              f"over {len(merged)} matched bars")
    elif max_diff < 50:
        print(f"  WARN — max diff {max_diff:.2f} bps, mean {mean_diff:.2f} bps "
              f"over {len(merged)} matched bars (investigate)")
    else:
        print(f"  FAIL — max diff {max_diff:.2f} bps (>50 bps), "
              f"mean {mean_diff:.2f} bps over {len(merged)} matched bars")

    print(f"\n{stats.to_string()}")
    return merged


# ---------------------------------------------------------------------------
# Full check suite
# ---------------------------------------------------------------------------

def run_all_checks(external_csv: str | None = None):
    """Run all quality checks and report results."""
    initialize_db()
    df = _load_bars()

    print(f"\nLoaded {len(df)} bars for {SYMBOL} {BAR_SIZE}")
    print(f"Range: {df.index.min()} -> {df.index.max()}")
    print(f"Trading days: {len(set(df.index.date))}")

    anomalous = check_rth_gaps(df)
    half_days = identify_half_days(df)
    dst_ok = check_dst_transitions(df)
    spot = spot_check_vs_yahoo(df)

    if external_csv:
        # Use the earliest IBKR date as the overlap start
        # (assumes external data covers the full range and IBKR covers recent)
        cross_source_parity(
            ibkr_start=str(df.index.min().date()),
            ibkr_end=str(df.index.max().date()),
            external_csv=external_csv,
        )

    # Summary
    print("\n" + "=" * 60)
    print("QUALITY CHECK SUMMARY")
    print("=" * 60)
    print(f"  Total bars:      {len(df)}")
    print(f"  Date range:      {df.index.min().date()} -> {df.index.max().date()}")
    print(f"  Trading days:    {len(set(df.index.date))}")
    print(f"  Full days:       {len(set(df.index.date)) - len(half_days) - len(anomalous)}")
    print(f"  Half days:       {len(half_days)}")
    print(f"  Anomalous days:  {len(anomalous)}")
    print(f"  DST consistent:  {'YES' if dst_ok else 'NO — investigate'}")
    print(f"  Yahoo spot-check: {'DONE' if spot is not None else 'SKIPPED'}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    external_csv = None
    if "--parity" in sys.argv and len(sys.argv) > sys.argv.index("--parity") + 1:
        external_csv = sys.argv[sys.argv.index("--parity") + 1]

    run_all_checks(external_csv=external_csv)


if __name__ == "__main__":
    main()
