"""
validate_cache.py

Compare cached market_data_bars against fresh IBKR pulls and report discrepancies.
Requires TWS running on the paper port.

Usage:
    python -m database.validate_cache SPY TRADES
    python -m database.validate_cache SPY ADJUSTED_LAST
    python -m database.validate_cache SPY TRADES --fix
"""

from __future__ import annotations

import sys

from database import market_data as mdb
from utils.log_config import setup_logger

logger = setup_logger(__name__)


def validate_symbol(
    symbol: str,
    what_to_show: str = "TRADES",
    duration: str = "30 Y",
    fix: bool = False,
) -> list[dict]:
    """Validate cached bars for a symbol against a fresh IBKR pull.

    If fix=True, the fresh bars are upserted (which now overwrites stale data
    thanks to ON CONFLICT DO UPDATE). Returns the list of discrepancies found
    *before* the fix.
    """
    from research.session import Session

    cached = mdb.get_bars(symbol, what_to_show=what_to_show)
    if cached is None or cached.empty:
        print(f"  No cached {what_to_show} bars for {symbol} — nothing to validate.")
        return []

    print(f"  Cached: {len(cached)} bars, {cached.index[0].date()} → {cached.index[-1].date()}")
    print(f"  Fetching fresh bars from IBKR (duration={duration})...")

    with Session() as session:
        df = session.market_data.get_bars(
            symbol, duration=duration, bar_size="1 day",
            what_to_show=what_to_show,
        )

    if df is None or df.empty:
        print("  IBKR returned no data — cannot validate.")
        return []

    fresh_bars = []
    for dt, row in df.iterrows():
        fresh_bars.append({
            "datetime": dt.strftime("%Y-%m-%d"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0)) if row.get("volume") is not None else None,
            "wap": float(row.get("wap", 0)) if row.get("wap") is not None else None,
            "bar_count": int(row["bar_count"]) if "bar_count" in row and row.get("bar_count") is not None else None,
        })

    print(f"  Fresh:  {len(fresh_bars)} bars, {fresh_bars[0]['datetime']} → {fresh_bars[-1]['datetime']}")

    diffs = mdb.validate_bars(symbol, fresh_bars, what_to_show=what_to_show)

    if not diffs:
        print(f"  CLEAN — all overlapping bars match.")
    else:
        dates_affected = sorted(set(d["bar_datetime"] for d in diffs))
        print(f"  DISCREPANCIES: {len(diffs)} field mismatches across {len(dates_affected)} dates")
        for d in diffs:
            print(f"    {d['bar_datetime']}  {d['field']:>10s}  cached={d['cached']}  fresh={d['fresh']}")

    if fix and diffs:
        print(f"  Applying fix (upsert {len(fresh_bars)} fresh bars)...")
        mdb.upsert_bars(symbol, fresh_bars, what_to_show=what_to_show)
        post_diffs = mdb.validate_bars(symbol, fresh_bars, what_to_show=what_to_show)
        if not post_diffs:
            print(f"  Fix verified — all bars now match.")
        else:
            print(f"  WARNING: {len(post_diffs)} discrepancies remain after fix.")

    return diffs


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: python -m database.validate_cache SYMBOL WHAT_TO_SHOW [--fix]")
        print("  e.g. python -m database.validate_cache SPY TRADES")
        print("       python -m database.validate_cache SPY ADJUSTED_LAST --fix")
        raise SystemExit(1)

    symbol = args[0]
    what_to_show = args[1]
    fix = "--fix" in args

    print(f"Validating {symbol} ({what_to_show})...")
    diffs = validate_symbol(symbol, what_to_show=what_to_show, fix=fix)
    if diffs:
        raise SystemExit(1)
