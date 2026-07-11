"""
Step 2 data loader for the intraday conditional strategy on QQQ.

Two modes:
  probe  — binary-search IBKR backward to find how far back QQQ 5-min bars go
  fetch  — pull the full 2015-01-01 -> present window into market_data_bars

Usage:
  python -m research.killed.intraday_conditional.fetch_5min_bars probe
  python -m research.killed.intraday_conditional.fetch_5min_bars fetch
  python -m research.killed.intraday_conditional.fetch_5min_bars load_csv PATH

The fetch command walks backward in monthly chunks, respecting the IBKR 60-req /
10-min pacing limit. Each chunk auto-upserts into market_data_bars via the
existing IBKRMarketDataClient pipeline (INSERT OR IGNORE on the UNIQUE key).
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from database import initialize_db
from database import market_data as _mdb
from research.session import Session
from utils.log_config import setup_logger

logger = setup_logger(__name__)

import functools
print = functools.partial(print, flush=True)

SYMBOL = "QQQ"
BAR_SIZE = "5 mins"
WHAT_TO_SHOW = "TRADES"
TARGET_START = "2015-01-01"

# Pacing: stay inside 60 requests / 10 minutes with margin.
_MAX_REQUESTS = 55
_WINDOW_SECONDS = 600


class _PacingLimiter:
    """Sliding-window rate limiter for IBKR historical data requests."""

    def __init__(self):
        self._timestamps: list[float] = []

    def wait(self):
        now = time.time()
        cutoff = now - _WINDOW_SECONDS
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= _MAX_REQUESTS:
            sleep_until = self._timestamps[0] + _WINDOW_SECONDS
            delay = sleep_until - now + 1.0
            print(f"  [pacing] {len(self._timestamps)} requests in window, "
                  f"sleeping {delay:.0f}s ...")
            time.sleep(delay)
        self._timestamps.append(time.time())


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def probe_depth(session: Session) -> str | None:
    """Find the earliest date with QQQ 5-min bar data on the connected TWS.

    Strategy: test one day near the start of each year (2015–2025), then
    refine the boundary month by month.  ~23 requests total.

    Returns the earliest date string (YYYY-MM-DD) found, or None.
    """
    provider = session.market_data.provider
    pacer = _PacingLimiter()

    first_trading_days = [
        "20150102", "20160104", "20170103", "20180102", "20190102",
        "20200102", "20210104", "20220103", "20230103", "20240102",
        "20250102", "20260102",
    ]

    print(f"\n=== Probing IBKR depth for {SYMBOL} {BAR_SIZE} ===\n")
    reachable = []

    for date_str in first_trading_days:
        pacer.wait()
        end_dt = f"{date_str} 23:59:59 US/Eastern"
        try:
            df = provider.get_historical_data(
                SYMBOL, duration="1 D", bar_size=BAR_SIZE,
                what_to_show=WHAT_TO_SHOW,
                end_date_time=end_dt, timeout=30,
            )
            if df is not None and not df.empty:
                reachable.append(date_str)
                print(f"  {date_str[:4]}: {len(df)} bars  OK")
            else:
                print(f"  {date_str[:4]}: no data")
        except Exception as e:
            print(f"  {date_str[:4]}: error — {e}")

    if not reachable:
        print("\nNo data found at any test date.")
        return None

    earliest_year = reachable[0]

    # Refine: walk backward month-by-month within the earliest year
    year = int(earliest_year[:4])
    earliest_date = f"{year}-01-02"
    for month in range(12, 0, -1):
        test = f"{year}{month:02d}01"
        if test >= earliest_year:
            continue
        pacer.wait()
        end_dt = f"{test} 23:59:59 US/Eastern"
        try:
            df = provider.get_historical_data(
                SYMBOL, duration="1 D", bar_size=BAR_SIZE,
                what_to_show=WHAT_TO_SHOW,
                end_date_time=end_dt, timeout=30,
            )
            if df is not None and not df.empty:
                earliest_date = f"{year}-{month:02d}-01"
                print(f"  {year}-{month:02d}: reachable ({len(df)} bars)")
            else:
                break
        except Exception:
            break

    # Also try one year earlier
    prev_year = year - 1
    for month in [12, 6, 1]:
        pacer.wait()
        test = f"{prev_year}{month:02d}02"
        end_dt = f"{test} 23:59:59 US/Eastern"
        try:
            df = provider.get_historical_data(
                SYMBOL, duration="1 D", bar_size=BAR_SIZE,
                what_to_show=WHAT_TO_SHOW,
                end_date_time=end_dt, timeout=30,
            )
            if df is not None and not df.empty:
                earliest_date = f"{prev_year}-{month:02d}-02"
                print(f"  {prev_year}-{month:02d}: reachable ({len(df)} bars)")
            else:
                print(f"  {prev_year}-{month:02d}: no data")
                if month == 12:
                    break
        except Exception:
            break

    print(f"\n=== Earliest reachable date: {earliest_date} ===")
    return earliest_date


# ---------------------------------------------------------------------------
# Determine max duration IBKR allows for 5-min bars
# ---------------------------------------------------------------------------

def _determine_chunk_duration(provider, pacer: _PacingLimiter) -> str:
    """Try "1 M" for 5-min bars; fall back to "1 W" if IBKR rejects it."""
    pacer.wait()
    try:
        df = provider.get_historical_data(
            SYMBOL, duration="1 M", bar_size=BAR_SIZE,
            what_to_show=WHAT_TO_SHOW, timeout=60,
        )
        if df is not None and not df.empty:
            print(f"  Duration '1 M' accepted ({len(df)} bars)")
            return "1 M"
    except ValueError:
        pass
    print("  '1 M' rejected, falling back to '1 W'")
    return "1 W"


# ---------------------------------------------------------------------------
# Full IBKR fetch
# ---------------------------------------------------------------------------

def fetch_ibkr(session: Session, target_start: str = TARGET_START) -> int:
    """Walk backward from today to target_start, fetching monthly chunks.

    Returns the total number of new bars inserted.
    """
    provider = session.market_data.provider
    pacer = _PacingLimiter()

    chunk_dur = _determine_chunk_duration(provider, pacer)
    chunk_delta = timedelta(days=30) if chunk_dur == "1 M" else timedelta(days=7)

    start_dt = datetime.strptime(target_start, "%Y-%m-%d")
    current_end = datetime.now()
    total_bars = 0
    chunk_num = 0

    print(f"\n=== Fetching {SYMBOL} {BAR_SIZE} from {target_start} -> today "
          f"(chunk={chunk_dur}) ===\n")

    while current_end >= start_dt:
        end_str = current_end.strftime("%Y%m%d") + " 23:59:59 US/Eastern"
        pacer.wait()
        chunk_num += 1

        try:
            df = provider.get_historical_data(
                SYMBOL, duration=chunk_dur, bar_size=BAR_SIZE,
                what_to_show=WHAT_TO_SHOW,
                end_date_time=end_str, timeout=60,
            )
        except ValueError:
            print(f"  chunk {chunk_num}: no data at {end_str}, stopping walk-back")
            break

        if df is None or df.empty:
            print(f"  chunk {chunk_num}: empty at {end_str}, stopping walk-back")
            break

        n = len(df)
        total_bars += n
        earliest = df.index.min()
        latest = df.index.max()
        print(f"  chunk {chunk_num}: {n} bars  "
              f"{earliest.strftime('%Y-%m-%d')} -> {latest.strftime('%Y-%m-%d')}")

        # Move end before the earliest bar in this chunk
        earliest_naive = earliest.to_pydatetime().replace(tzinfo=None)
        current_end = earliest_naive - timedelta(days=1)

    print(f"\nDone. {total_bars} bars fetched across {chunk_num} chunks.")
    print("(Duplicates handled by INSERT OR IGNORE in upsert_bars)")

    # Report what's in the DB now
    cached = _mdb.get_bars(SYMBOL, bar_size=BAR_SIZE, what_to_show=WHAT_TO_SHOW)
    if cached is not None:
        print(f"\nDB total: {len(cached)} bars, "
              f"{cached.index.min()} -> {cached.index.max()}")
    return total_bars


# ---------------------------------------------------------------------------
# CSV loader (for FirstRate Data or Polygon export)
# ---------------------------------------------------------------------------

def load_csv(path: str) -> int:
    """Load 5-min QQQ bars from a CSV file into market_data_bars.

    Auto-detects common CSV formats:
      - "DateTime,Open,High,Low,Close,Volume"  (FirstRate)
      - "datetime,open,high,low,close,volume"  (generic)
      - "Date,Time,Open,High,Low,Close,Volume" (two-column timestamp)

    Returns the number of new rows inserted.
    """
    print(f"\n=== Loading CSV: {path} ===\n")
    df = pd.read_csv(path)

    # Normalize column names
    df.columns = [c.strip().lower() for c in df.columns]

    # Handle two-column timestamp
    if "date" in df.columns and "time" in df.columns:
        df["datetime"] = df["date"].astype(str) + " " + df["time"].astype(str)
        df = df.drop(columns=["date", "time"])

    required = {"datetime", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")

    df["datetime"] = pd.to_datetime(df["datetime"])

    # Convert to bar dicts matching upsert_bars format
    bars = []
    for _, row in df.iterrows():
        bars.append({
            "datetime": row["datetime"].strftime("%Y%m%d %H:%M:%S"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0)),
            "wap": float(row["wap"]) if "wap" in row and pd.notna(row.get("wap")) else None,
            "bar_count": int(row["bar_count"]) if "bar_count" in row and pd.notna(row.get("bar_count")) else None,
        })

    # Upsert in batches
    BATCH = 5000
    total_inserted = 0
    for i in range(0, len(bars), BATCH):
        batch = bars[i:i + BATCH]
        n = _mdb.upsert_bars(
            symbol=SYMBOL, bars=batch, sec_type="STK",
            bar_size=BAR_SIZE, what_to_show=WHAT_TO_SHOW,
        )
        total_inserted += n

    print(f"Loaded {len(bars)} rows from CSV, {total_inserted} new inserts.")

    cached = _mdb.get_bars(SYMBOL, bar_size=BAR_SIZE, what_to_show=WHAT_TO_SHOW)
    if cached is not None:
        print(f"DB total: {len(cached)} bars, "
              f"{cached.index.min()} -> {cached.index.max()}")
    return total_inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    initialize_db()

    if len(sys.argv) < 2:
        print("Usage: python -m research.killed.intraday_conditional.fetch_5min_bars "
              "<probe|fetch|load_csv> [args]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "load_csv":
        if len(sys.argv) < 3:
            print("Usage: ... load_csv PATH")
            sys.exit(1)
        load_csv(sys.argv[2])
        return

    # probe and fetch need TWS
    with Session() as session:
        if cmd == "probe":
            result = probe_depth(session)
            if result:
                earliest_year = int(result[:4])
                if earliest_year <= 2015:
                    print("\nIBKR reaches 2015 — use IBKR as sole source.")
                    print("Next: python -m research.killed.intraday_conditional.fetch_5min_bars fetch")
                else:
                    print(f"\nIBKR tops out at ~{result}. External source needed "
                          f"for {TARGET_START} -> {result}.")
                    print("Pull external CSV, then:")
                    print("  python -m research.killed.intraday_conditional.fetch_5min_bars load_csv PATH")
                    print("  python -m research.killed.intraday_conditional.fetch_5min_bars fetch")
                    print("(fetch will fill the IBKR-reachable portion for overlap validation)")

        elif cmd == "fetch":
            fetch_ibkr(session)

        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)


if __name__ == "__main__":
    main()
