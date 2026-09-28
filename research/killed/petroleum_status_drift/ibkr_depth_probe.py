"""
IBKR CL (full-size WTI Crude Oil futures) historical depth probe for the
petroleum-status-drift strategy, plus the daily-bar fetch/cache path.

Conceptually mirrors the QQQ 5-min depth probe
(research/killed/intraday_conditional/fetch_5min_bars.py): walk backward and find
the earliest reachable date. The *mechanism* differs out of necessity --
IBKR rejects `endDateTime` on a CONTFUT request outright (error 10339,
"Setting end date/time for continuous future security type is not
allowed", confirmed empirically against live TWS), so CONTFUT can only
answer "how far back does today's spliced front-month series reach in one
shot", not "what does the market look like as of an arbitrary past date"
-- there is no way to paginate a CONTFUT request backward the way the QQQ
probe pages back through STK bars.

The walk-back therefore uses individual **expired FUT contracts**
(`includeExpired=True`, `lastTradeDateOrContractMonth="YYYYMM"`, endDateTime
anchored near that contract's own expiration -- see
`_approx_expiry_end_date_time` and the `_CANDIDATE_YEARS` comment for why
*not* anchoring it, which is what research/killed/diversified_trend/ibkr_data_probe.py's
early-2005 cross-check does, fails silently instead of erroring). A CONTFUT
max-duration pull is run alongside as a second, independent read: it
reports what IBKR's own front-month splice reaches in one request, which is
the more directly useful number for Step 3 (that's the series a signal
would actually be computed on) but can differ from the raw per-contract
depth in either direction. **Neither read is sufficient on its own** --
see the CAUTION below; the CONTFUT date range in particular understates
how little of it is trustworthy.

CAUTION -- date range reached is not the same as usable data. This
account's CONTFUT pull for CL returns bars across its whole 2018-01-24 -->
present range, but ~73% of the bars before 2024-09-17 are degenerate:
open == high == low == close with volume == 0 (confirmed empirically,
including on 2020-04-20 -- the day front-month WTI famously settled
negative -- where the cached bar reads a flat, positive $38.83). Only
2024-09-17 onward is a fully dense, real OHLCV series (486/486 bars clean
as of this check). `report_g0()` below filters degenerate bars out before
counting; anyone querying market_data_bars directly for this symbol must
do the same or risk building on fabricated-looking price history that
predates real coverage. Root cause not fully diagnosed -- most likely this
account/subscription lacks full NYMEX energy historical entitlement and
IBKR silently backfills a stale-snapshot placeholder rather than erroring;
flagged in docs/IBKR_NOTES.md as a general gotcha for any future futures
data pull, not just this one.

Research is done on full-size CL (per RESEARCH_WORKFLOW_petroleum_status_drift.md
Step 2 -- CL is the more liquid series with deeper IBKR history than MCL,
which only launched in 2019). Execution instrument is MCL; that's a sizing
choice made downstream, not a data-source choice.

Commands
--------
    python -m research.killed.petroleum_status_drift.ibkr_depth_probe probe
    python -m research.killed.petroleum_status_drift.ibkr_depth_probe fetch
    python -m research.killed.petroleum_status_drift.ibkr_depth_probe report

`probe`  -- find the earliest reachable date, print a summary. Does not write
            to market_data_bars.
`fetch`  -- walk backward from today to the depth found by `probe` (or
            TARGET_START, whichever is later), caching daily CONTFUT bars
            into market_data_bars (sec_type="FUT").
`report` -- join the cached CL depth against the cached EIA release calendar
            (research/killed/petroleum_status_drift/data/eia_weekly_crude_stocks.csv,
            built by eia_fetch.py) and report the G0 event count: number of
            EIA release dates that fall within the CL bar coverage window,
            vs. the pre-committed floor of 200 (RESEARCH_WORKFLOW_petroleum_status_drift.md).
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from ibapi.contract import Contract

from database import initialize_db
from database import market_data as _mdb
from research.session import Session
from utils.log_config import setup_logger

logger = setup_logger(__name__)

import functools
print = functools.partial(print, flush=True)

SYMBOL = "CL"
EXCHANGE = "NYMEX"
CURRENCY = "USD"
BAR_SIZE = "1 day"
WHAT_TO_SHOW = "TRADES"
TARGET_START = "2005-01-01"  # aspirational floor; CL futures trade back to 1983,
                              # but no expectation IBKR's own history reaches that far

_CSV_PATH = Path(__file__).parent / "data" / "eia_weekly_crude_stocks.csv"

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


def _make_contfut() -> Contract:
    c = Contract()
    c.symbol = SYMBOL
    c.secType = "CONTFUT"
    c.exchange = EXCHANGE
    c.currency = CURRENCY
    return c


def _make_fut(contract_month: str) -> Contract:
    c = Contract()
    c.symbol = SYMBOL
    c.secType = "FUT"
    c.exchange = EXCHANGE
    c.currency = CURRENCY
    c.lastTradeDateOrContractMonth = contract_month
    c.includeExpired = True
    return c


def _fetch_raw(app, contract, duration: str, end_date_time: str = "",
               timeout: int = 45):
    """Fire reqHistoricalData directly (bypasses MarketDataService, which is
    STK-only via ContractHandler) and block until data or timeout."""
    app.historical_data = []
    app._historical_data_event.clear()

    app.reqHistoricalData(
        reqId=3101,
        contract=contract,
        endDateTime=end_date_time,
        durationStr=duration,
        barSizeSetting=BAR_SIZE,
        whatToShow=WHAT_TO_SHOW,
        useRTH=1,
        formatDate=1,
        keepUpToDate=False,
        chartOptions=[],
    )

    got_data = app._historical_data_event.wait(timeout=timeout)
    if not got_data or not app.historical_data:
        return None

    df = pd.DataFrame(app.historical_data)
    df["datetime"] = pd.to_datetime(df["datetime"], format="%Y%m%d")
    df = df.set_index("datetime").sort_index()
    return df


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

_CONTFUT_DURATIONS = ["50 Y", "30 Y", "20 Y", "15 Y", "10 Y", "5 Y", "2 Y"]
_PROBE_MONTH = 3  # March contract -- roughly front/near-month when testing January

# Coarse cross-check grid, not an exhaustive walk. An earlier version of this
# probe tested every year 1988-2026 with no endDateTime and got "No security
# definition has been found" (error 200) for EVERY year including recent
# ones -- because omitting endDateTime for an EXPIRED contract makes IBKR
# anchor the request at *now*, not at the contract's own last trading day,
# so of course a contract that stopped trading years ago has "no data" in a
# window ending today. Fixed below by anchoring endDateTime near each
# contract's approximate expiration. Once fixed, empirically confirmed
# (live TWS, 2026-08-24): the most recent contract resolves fine, but 2017,
# 2010, and 2005 all still come back "No security definition found" -- this
# account cannot resolve individual dated CL FUT contracts by symbol+month
# beyond roughly the CONTFUT splice's own boundary (see _probe_contfut_max).
# A handful of spaced-out years is enough to confirm/refute that boundary
# without paying for a 39-request walk that mostly reconfirms the same wall.
_CANDIDATE_YEARS = [datetime.now().year - k for k in (0, 1, 2, 3, 5, 8, 12, 18, 25)]


def _probe_contfut_max(app, pacer: _PacingLimiter):
    """Single CONTFUT request per duration, largest-first, until one returns
    data. No endDateTime (IBKR rejects it for CONTFUT) -- this reports
    whatever IBKR's own front-month splice reaches from 'now' backward in
    one shot, which is the number Step 3 would actually build a signal on.
    """
    contract = _make_contfut()
    for dur in _CONTFUT_DURATIONS:
        pacer.wait()
        df = _fetch_raw(app, contract, duration=dur, timeout=45)
        if df is not None and not df.empty:
            return df, dur
    return None, None


def _approx_expiry_end_date_time(year: int, month: int) -> str:
    """Rough anchor for an expired CL contract's last trading day: CME's
    actual rule is ~3 business days before the 25th of the month PRIOR to
    delivery; day-28-of-prior-month is a safe upper bound (a few days of
    slack costs nothing against a "1 M" duration request, and precision
    doesn't matter for a reachability probe).
    """
    prior = pd.Timestamp(year=year, month=month, day=1) - pd.DateOffset(months=1)
    anchor = prior.replace(day=28)
    return f"{anchor.strftime('%Y%m%d')} 23:59:59 US/Eastern"


def _probe_fut_year(app, pacer: _PacingLimiter, year: int, month: int = _PROBE_MONTH,
                     timeout: int = 12):
    """Test whether IBKR has data for the expired FUT contract expiring
    around year/month, anchoring endDateTime near that contract's own
    expiration (see _approx_expiry_end_date_time and the _CANDIDATE_YEARS
    comment above for why omitting endDateTime doesn't work here).

    Short timeout: both the success and failure paths (200 "no security
    definition", empty historicalData) resolve near-instantly against a live
    TWS -- IBApp.error() doesn't set _historical_data_event on error codes,
    so a genuine non-response would otherwise cost the full default timeout
    per candidate year.
    """
    contract_month = f"{year:04d}{month:02d}"
    end_dt = _approx_expiry_end_date_time(year, month)
    pacer.wait()
    return _fetch_raw(app, _make_fut(contract_month), duration="1 M",
                       end_date_time=end_dt, timeout=timeout)


def probe_depth(session: Session) -> str | None:
    """Find the earliest reachable CL date on the connected TWS via two
    independent reads: a CONTFUT max-duration splice, and a year-by-year
    walk back through individual expired FUT contracts (see module
    docstring for why CONTFUT can't itself be walked backward).

    Returns the earliest date string (YYYY-MM-DD) found by the FUT walk, or
    None if no year was reachable.
    """
    app = session._app
    pacer = _PacingLimiter()

    print(f"\n=== CONTFUT max-duration splice ===\n")
    cf_df, cf_dur = _probe_contfut_max(app, pacer)
    if cf_df is not None:
        print(f"  CONTFUT ({cf_dur}): {len(cf_df)} bars, "
              f"{cf_df.index.min().date()} -> {cf_df.index.max().date()}")
    else:
        print("  CONTFUT: no data returned at any duration")

    descending_years = list(reversed(_CANDIDATE_YEARS))
    print(f"\n=== Per-contract-month FUT walk-back, {descending_years[0]} descending to "
          f"{descending_years[-1]} (month={_PROBE_MONTH:02d} contract each year, "
          f"stop after 3 consecutive misses) ===\n")

    reachable = []
    consecutive_misses = 0
    for year in descending_years:
        df = _probe_fut_year(app, pacer, year)
        if df is not None and not df.empty:
            reachable.append((year, df.index.min(), df.index.max(), len(df)))
            print(f"  {year}: {len(df)} bars, {df.index.min().date()} -> {df.index.max().date()}  OK")
            consecutive_misses = 0
        else:
            print(f"  {year}: no data")
            consecutive_misses += 1
            if reachable and consecutive_misses >= 3:
                print(f"  (3 consecutive misses after a hit -- stopping walk-back here; "
                      f"years below {year + 3} not probed)")
                break

    if not reachable:
        print("\nNo data found for any candidate year.")
        return cf_df.index.min().date().isoformat() if cf_df is not None else None

    earliest_year, earliest_min, _, _ = min(reachable, key=lambda r: r[0])

    # Refine: test all 12 months of the year before the earliest reachable
    # year, to see whether the boundary falls mid-year rather than at Jan 1.
    prev_year = earliest_year - 1
    print(f"\n=== Refining boundary within {prev_year} (all 12 contract months) ===\n")
    prev_year_hits = []
    for month in range(1, 13):
        df = _probe_fut_year(app, pacer, prev_year, month)
        if df is not None and not df.empty:
            prev_year_hits.append((month, df.index.min()))
            print(f"  {prev_year}-{month:02d}: {len(df)} bars, "
                  f"{df.index.min().date()} -> {df.index.max().date()}  OK")
        else:
            print(f"  {prev_year}-{month:02d}: no data")

    if prev_year_hits:
        boundary_min = min(d for _, d in prev_year_hits)
        earliest_min = min(earliest_min, boundary_min)

    earliest_date = earliest_min.date().isoformat()
    print(f"\n=== FUT walk-back earliest reachable date: {earliest_date} ===")

    if cf_df is not None:
        cf_earliest = cf_df.index.min().date().isoformat()
        print(f"=== CONTFUT splice earliest date: {cf_earliest} ({cf_dur} duration) ===")
        if cf_earliest > earliest_date:
            print(f"  NOTE: raw per-contract FUT data reaches further back "
                  f"({earliest_date}) than the CONTFUT splice ({cf_earliest}). "
                  f"A continuous series for Step 3 signal work may need "
                  f"per-contract stitching rather than IBKR's own CONTFUT "
                  f"if the deeper window matters (this is separate from "
                  f"OD#3's front-contract-only PnL-series decision, which "
                  f"is about roll handling, not depth).")

    return earliest_date


# ---------------------------------------------------------------------------
# Fetch (cache into market_data_bars)
# ---------------------------------------------------------------------------

def fetch_ibkr(session: Session) -> int:
    """Cache CL CONTFUT daily bars into market_data_bars (sec_type='FUT',
    matching what upsert_bars records for the diversified_trend CONTFUT
    pulls -- CONTFUT contracts don't carry their own distinct sec_type worth
    keying on here).

    CONTFUT rejects endDateTime (see module docstring), so this is a single
    max-duration request, not a chunked walk-back -- whatever IBKR's front-
    month splice returns in one shot is what gets cached. Re-running is
    idempotent (upsert_bars dedups on the UNIQUE key).
    """
    app = session._app
    pacer = _PacingLimiter()

    print(f"\n=== Fetching {SYMBOL} CONTFUT {BAR_SIZE} (max duration) ===\n")
    df, dur = _probe_contfut_max(app, pacer)
    if df is None:
        print("  No data returned at any duration -- nothing cached.")
        return 0

    n = len(df)
    print(f"  {dur}: {n} bars  {df.index.min().date()} -> {df.index.max().date()}")

    try:
        _mdb.upsert_bars(
            symbol=SYMBOL,
            bars=app.historical_data,
            sec_type="FUT",
            bar_size=BAR_SIZE,
            what_to_show=WHAT_TO_SHOW,
        )
    except Exception as e:
        logger.warning(f"DB upsert_bars failed: {e}")
        return 0

    cached = _mdb.get_bars(SYMBOL, sec_type="FUT", bar_size=BAR_SIZE, what_to_show=WHAT_TO_SHOW)
    if cached is not None:
        print(f"DB total: {len(cached)} bars, {cached.index.min()} -> {cached.index.max()}")
    return n


# ---------------------------------------------------------------------------
# Report: G0 event-count gate
# ---------------------------------------------------------------------------

def _flat_bar_mask(bars: pd.DataFrame) -> pd.Series:
    """True where a bar is degenerate: open==high==low==close and volume==0.

    Discovered empirically in this cached CL series (see module CAUTION):
    ~73% of bars before 2024-09-17 are like this, including 2020-04-20 (the
    day front-month WTI famously settled negative) reading a flat, positive
    $38.83 -- clearly not real market data, not just a rounding artifact.
    """
    return ((bars["open"] == bars["high"]) & (bars["high"] == bars["low"])
             & (bars["low"] == bars["close"]) & (bars["volume"] == 0))


def report_g0():
    """Join cached CL bar coverage against the cached EIA release calendar
    and report the G0 gate: total usable events vs. the floor of 200
    (RESEARCH_WORKFLOW_petroleum_status_drift.md).

    Filters degenerate (flat-OHLC, zero-volume) bars out first -- counting
    events against the raw date range overstates coverage by ~4.4x on this
    account's data (see module CAUTION and _flat_bar_mask).
    """
    cached = _mdb.get_bars(SYMBOL, sec_type="FUT", bar_size=BAR_SIZE, what_to_show=WHAT_TO_SHOW)
    if cached is None or cached.empty:
        print(f"No cached {SYMBOL} bars in market_data_bars yet -- run `fetch` first.")
        return

    if not _CSV_PATH.exists():
        print(f"No cached EIA calendar at {_CSV_PATH} -- run "
              f"`python -m research.killed.petroleum_status_drift.eia_fetch fetch` first.")
        return

    eia = pd.read_csv(_CSV_PATH, parse_dates=["period", "release_date"])

    flat = _flat_bar_mask(cached)
    n_flat = int(flat.sum())

    price_start, price_end = cached.index.min(), cached.index.max()
    raw_usable = eia[(eia["release_date"] >= price_start) & (eia["release_date"] < price_end)]

    print(f"\n=== G0 event-depth report ===")
    print(f"CL raw price coverage: {price_start.date()} -> {price_end.date()} ({len(cached)} bars, "
          f"{n_flat} degenerate/flat -- {n_flat / len(cached):.0%})")
    print(f"EIA calendar coverage: {eia['period'].min().date()} -> {eia['period'].max().date()} "
          f"({len(eia)} releases)")

    if n_flat == 0:
        print(f"Usable events: {len(raw_usable)}  |  G0 floor: 200  |  "
              f"G0: {'PASS' if len(raw_usable) >= 200 else 'FAIL'}")
        return

    print(f"\nWARNING: raw-window event count above is NOT valid -- {n_flat} of "
          f"{len(cached)} cached bars are degenerate. Recomputing against the "
          f"dense, clean-only window instead.")

    # The clean-data window is everything strictly after the LAST flat bar --
    # an event study needs several consecutive clean days around each
    # release, not just isolated clean bars scattered inside an otherwise
    # contaminated stretch.
    last_flat_date = cached.index[flat][-1]
    clean = cached[cached.index > last_flat_date]
    clean_start, clean_end = clean.index.min(), clean.index.max()
    clean_usable = eia[(eia["release_date"] >= clean_start) & (eia["release_date"] < clean_end)]

    print(f"Clean price coverage: {clean_start.date()} -> {clean_end.date()} "
          f"({len(clean)} bars, {int(_flat_bar_mask(clean).sum())} flat)")
    print(f"Usable events (clean window): {len(clean_usable)}")
    print(f"G0 floor: 200")
    print(f"G0: {'PASS' if len(clean_usable) >= 200 else 'FAIL'}")


def main():
    initialize_db()

    if len(sys.argv) < 2 or sys.argv[1] not in ("probe", "fetch", "report"):
        print("Usage: python -m research.killed.petroleum_status_drift.ibkr_depth_probe <probe|fetch|report>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "report":
        report_g0()
        return

    with Session() as session:
        if cmd == "probe":
            probe_depth(session)
        elif cmd == "fetch":
            fetch_ibkr(session)


if __name__ == "__main__":
    main()
