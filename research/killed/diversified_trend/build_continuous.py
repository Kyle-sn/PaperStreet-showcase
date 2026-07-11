"""
Build continuous-contract futures series from Databento raw .dbn.zst files.

Pipeline:
  1. Parse definition files -> contract metadata (expiry, root)
  2. Parse statistics files -> daily settlement prices
  3. Build deterministic roll calendar
  4. Construct signal (ratio-adjusted) and PnL (actual-contract) series
  4b. Validate raw build, then collapse CME Sunday pseudo-settlements (canonical)
  5. Persist Sunday-free series to SQLite (research layer)

Usage:
    python -m research.killed.diversified_trend.build_continuous              # full rebuild
    python -m research.killed.diversified_trend.build_continuous --recollapse # migrate-only
"""

import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOTS = ["ES", "ZN", "6E", "6J", "6A", "CL", "GC", "HG"]

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "databento"
STATS_DIR = RAW_DIR / "statistics"
DEFS_DIR = RAW_DIR / "definition"
RESEARCH_DB = Path(__file__).resolve().parent.parent.parent / "data" / "futures_research.db"

# CME contract multipliers (dollars per point of price movement)
MULTIPLIERS = {
    "ES": 50.0,       # $50 × index points
    "ZN": 1000.0,     # $1,000 per point (face $100k, quoted in points)
    "6E": 125_000.0,  # 125,000 EUR
    "6J": 12_500_000.0,  # 12,500,000 JPY
    "6A": 100_000.0,  # 100,000 AUD
    "CL": 1_000.0,    # 1,000 barrels
    "GC": 100.0,      # 100 troy oz
    "HG": 25_000.0,   # 25,000 lbs
}

# Minimum tick sizes (for reference / validation)
TICK_SIZES = {
    "ES": 0.25,
    "ZN": 0.015625,   # 1/64 of a point
    "6E": 0.00005,    # half-pip  ($6.25/tick)
    "6J": 0.0000005,  # half-pip equivalent ($6.25/tick)
    "6A": 0.00005,    # half-pip ($5/tick) — actually 0.0001 = $10 min
    "CL": 0.01,       # 1 cent/bbl ($10/tick)
    "GC": 0.10,       # 10 cents/oz ($10/tick)
    "HG": 0.0005,     # 0.05 cents/lb ($12.50/tick)
}

# Month code -> month number
MONTH_CODES = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}

# Which month codes each root trades (for identifying the front contract)
ROOT_MONTHS = {
    "ES": "HMUZ",     # quarterly
    "ZN": "HMUZ",     # quarterly
    "6E": "HMUZ",     # quarterly
    "6J": "HMUZ",     # quarterly
    "6A": "HMUZ",     # quarterly
    "CL": "FGHJKMNQUVXZ",  # monthly
    "GC": "GJMQVZ",   # even months (Feb, Apr, Jun, Aug, Oct, Dec)
    "HG": "FGHJKMNQUVXZ",  # monthly
}

# Roll method per root
# "before_expiry": roll N bdays before last-trade-date
# "before_first_notice": roll N bdays before first-notice-date
ROLL_METHOD = {
    "ES": ("before_expiry", 5),
    "ZN": ("before_first_notice", 3),
    "6E": ("before_expiry", 5),
    "6J": ("before_expiry", 5),
    "6A": ("before_expiry", 5),
    "CL": ("before_expiry", 5),  # CL expiry is already well before delivery
    "GC": ("before_first_notice", 3),
    "HG": ("before_first_notice", 3),
}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS futures_contracts (
    raw_symbol      TEXT NOT NULL,
    root            TEXT NOT NULL,
    expiration      TEXT NOT NULL,
    delivery_month  TEXT NOT NULL,
    multiplier      REAL NOT NULL,
    tick_size       REAL NOT NULL,
    PRIMARY KEY (raw_symbol)
);

CREATE TABLE IF NOT EXISTS futures_settlements (
    raw_symbol      TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    settlement      REAL NOT NULL,
    PRIMARY KEY (raw_symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS futures_roll_calendar (
    root            TEXT NOT NULL,
    roll_date       TEXT NOT NULL,
    from_symbol     TEXT NOT NULL,
    to_symbol       TEXT NOT NULL,
    PRIMARY KEY (root, roll_date)
);

CREATE TABLE IF NOT EXISTS futures_continuous (
    root            TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    active_symbol   TEXT NOT NULL,
    raw_settle      REAL NOT NULL,
    signal_price    REAL NOT NULL,
    pnl_daily_usd   REAL,
    is_roll         INTEGER NOT NULL DEFAULT 0,
    sample          TEXT NOT NULL DEFAULT 'IS',
    PRIMARY KEY (root, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_settlements_root
    ON futures_settlements(raw_symbol);
CREATE INDEX IF NOT EXISTS idx_continuous_sample
    ON futures_continuous(root, sample, trade_date);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_symbol(sym: str) -> tuple[str, int, int] | None:
    """Parse a CME futures symbol like 'ESM0' -> (root, month, year_2digit).

    Returns None for spreads or unrecognizable symbols.
    """
    if "-" in sym or ":" in sym or " " in sym:
        return None
    # Try known roots (longest first to avoid prefix collision)
    for root in sorted(ROOTS, key=len, reverse=True):
        if sym.startswith(root):
            rest = sym[len(root):]
            if len(rest) >= 2:
                month_code = rest[0]
                year_code = rest[1:]
                if month_code in MONTH_CODES and year_code.isdigit():
                    return root, MONTH_CODES[month_code], int(year_code)
    return None


def resolve_year(year_1digit: int, reference_year: int) -> int:
    """Resolve single-digit year code to full year using decade of reference.

    CME uses rolling decade codes: ESM6 in 2026 context = 2026,
    but ESM6 in 2016 context = 2016.
    """
    decade = (reference_year // 10) * 10
    candidate = decade + year_1digit
    # If candidate is more than 5 years before reference, it's next decade
    if candidate < reference_year - 5:
        candidate += 10
    # If candidate is more than 5 years after reference, it's prev decade
    elif candidate > reference_year + 5:
        candidate -= 10
    return candidate


def business_days_before(d: date, n: int) -> date:
    """Return the date that is n business days before d (simple weekday check)."""
    count = 0
    current = d
    while count < n:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            count += 1
    return current


def last_business_day_of_month(year: int, month: int) -> date:
    """Return the last business day of the given month."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def compute_first_notice_date(root: str, delivery_year: int, delivery_month: int) -> date:
    """Approximate first-notice/first-position date for deliverable futures.

    ZN: last business day of month preceding delivery month.
    GC: last business day of month preceding delivery month.
    HG: last business day of month preceding delivery month.
    """
    if delivery_month == 1:
        prev_year, prev_month = delivery_year - 1, 12
    else:
        prev_year, prev_month = delivery_year, delivery_month - 1
    return last_business_day_of_month(prev_year, prev_month)


# ---------------------------------------------------------------------------
# Step 1: Parse definitions -> contract metadata
# ---------------------------------------------------------------------------

def load_definitions() -> pd.DataFrame:
    """Parse all definition files and extract contract metadata for our roots.

    Single-digit year codes cycle every decade (ESM0 = Jun 2010 AND Jun 2020),
    so raw_symbol alone is not a unique identifier. We derive the full year
    from Databento's expiration timestamp and key by (raw_symbol, full_year).
    """
    print("Loading definition files...")
    files = sorted(DEFS_DIR.glob("*.definition.dbn.zst"))
    print(f"  Found {len(files)} definition files")

    # Key: (raw_symbol, expiration_iso) to deduplicate across daily files
    all_contracts: dict[tuple[str, str], dict] = {}

    for i, fpath in enumerate(files):
        if i % 500 == 0:
            print(f"  Processing definition file {i}/{len(files)}...")
        try:
            store = db.DBNStore.from_file(str(fpath))
            df = store.to_df()
        except Exception as e:
            print(f"  WARNING: Failed to parse {fpath.name}: {e}")
            continue

        futures = df[df["instrument_class"] == "F"]
        if futures.empty:
            continue

        ours = futures[futures["group"].isin(ROOTS)]
        if ours.empty:
            continue

        for _, row in ours.iterrows():
            sym = row["raw_symbol"]
            parsed = parse_symbol(sym)
            if parsed is None:
                continue
            root, month_num, _ = parsed

            exp_ts = row["expiration"]
            if pd.isna(exp_ts):
                continue
            exp_date = pd.Timestamp(exp_ts).date()
            exp_iso = exp_date.isoformat()

            dedup_key = (sym, exp_iso)
            if dedup_key in all_contracts:
                continue

            # Delivery month is AFTER expiration (you deliver after trading ends).
            # If the month code is earlier in the year than the expiry month,
            # delivery is in the next calendar year (e.g. CLF1 = Jan delivery,
            # expires Dec of the prior year).
            if month_num >= exp_date.month:
                full_year = exp_date.year
            else:
                full_year = exp_date.year + 1
            delivery_month_str = f"{full_year}-{month_num:02d}"

            all_contracts[dedup_key] = {
                "raw_symbol": sym,
                "root": root,
                "expiration": exp_iso,
                "delivery_month": delivery_month_str,
                "delivery_year": full_year,
                "delivery_month_num": month_num,
                "multiplier": MULTIPLIERS[root],
                "tick_size": TICK_SIZES[root],
            }

    contracts_df = pd.DataFrame(all_contracts.values())
    print(f"  Extracted {len(contracts_df)} unique contracts across {len(ROOTS)} roots")
    for root in ROOTS:
        n = (contracts_df["root"] == root).sum()
        print(f"    {root}: {n} contracts")
    return contracts_df


# ---------------------------------------------------------------------------
# Step 2: Parse statistics -> settlement prices
# ---------------------------------------------------------------------------

def load_settlements(contracts_df: pd.DataFrame) -> pd.DataFrame:
    """Parse all statistics files and extract daily settlement prices."""
    print("\nLoading settlement prices...")
    files = sorted(STATS_DIR.glob("*.statistics.dbn.zst"))
    print(f"  Found {len(files)} statistics files")

    known_symbols = set(contracts_df["raw_symbol"])
    all_settles = []
    t0 = time.time()

    for i, fpath in enumerate(files):
        if i % 500 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (len(files) - i) / rate if rate > 0 else 0
            print(f"  Processing statistics file {i}/{len(files)} "
                  f"({elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining)...")
        try:
            store = db.DBNStore.from_file(str(fpath))
            df = store.to_df()
        except Exception as e:
            print(f"  WARNING: Failed to parse {fpath.name}: {e}")
            continue

        # Filter: settlement prices only (stat_type == 3)
        settle = df[df["stat_type"] == 3].copy()
        if settle.empty:
            continue

        # Filter to our known symbols
        settle = settle[settle["symbol"].isin(known_symbols)]
        if settle.empty:
            continue

        # Extract trade date from file name
        fname = fpath.stem
        file_date_str = fname.split("-")[2].split(".")[0]
        trade_date = f"{file_date_str[:4]}-{file_date_str[4:6]}-{file_date_str[6:8]}"

        # Take the LAST settlement record per symbol (most final/official)
        settle = settle.sort_index()  # ts_recv order
        last_per_sym = settle.groupby("symbol").last().reset_index()

        for _, row in last_per_sym.iterrows():
            price = row["price"]
            if price <= 0 or np.isnan(price):
                continue
            all_settles.append({
                "raw_symbol": row["symbol"],
                "trade_date": trade_date,
                "settlement": price,
            })

    elapsed = time.time() - t0
    settles_df = pd.DataFrame(all_settles)
    print(f"  Loaded {len(settles_df)} settlement records in {elapsed:.1f}s")

    # Summary per root — use deduplicated root mapping (same raw_symbol always = same root)
    sym_to_root = contracts_df[["raw_symbol", "root"]].drop_duplicates(subset="raw_symbol")
    settles_df = settles_df.merge(sym_to_root, on="raw_symbol", how="left")
    for root in ROOTS:
        sub = settles_df[settles_df["root"] == root]
        n_dates = sub["trade_date"].nunique()
        n_syms = sub["raw_symbol"].nunique()
        dates = sorted(sub["trade_date"].unique())
        print(f"    {root}: {n_syms} contracts, {n_dates} dates, "
              f"{dates[0] if dates else 'N/A'} to {dates[-1] if dates else 'N/A'}")

    return settles_df


# ---------------------------------------------------------------------------
# Step 3: Roll calendar
# ---------------------------------------------------------------------------

def build_roll_calendar(
    contracts_df: pd.DataFrame,
    settles_df: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Build deterministic roll calendar per root.

    Returns dict: root -> DataFrame with columns [trade_date, active_symbol].
    """
    print("\nBuilding roll calendars...")
    calendars = {}

    for root in ROOTS:
        root_contracts = contracts_df[contracts_df["root"] == root].copy()
        root_settles = settles_df[settles_df["root"] == root].copy()

        if root_contracts.empty or root_settles.empty:
            print(f"  {root}: NO DATA — skipping")
            continue

        # Valid month codes for this root
        valid_months = {MONTH_CODES[c] for c in ROOT_MONTHS.get(root, "")}

        # Filter contracts to only the standard delivery months
        root_contracts = root_contracts[
            root_contracts["delivery_month_num"].isin(valid_months)
        ].copy()

        # Compute roll-off date for each contract
        method, n_days = ROLL_METHOD[root]
        roll_dates = []
        for _, c in root_contracts.iterrows():
            if method == "before_expiry":
                if c["expiration"] is None:
                    continue
                exp = date.fromisoformat(c["expiration"])
                roll_off = business_days_before(exp, n_days)
            else:  # before_first_notice
                fnd = compute_first_notice_date(
                    root, c["delivery_year"], c["delivery_month_num"]
                )
                roll_off = business_days_before(fnd, n_days)
            roll_dates.append({
                "raw_symbol": c["raw_symbol"],
                "delivery_month": c["delivery_month"],
                "expiration": c["expiration"],
                "roll_off_date": roll_off.isoformat(),
            })

        roll_df = pd.DataFrame(roll_dates)
        if roll_df.empty:
            print(f"  {root}: No roll dates computed — skipping")
            continue

        roll_df = roll_df.sort_values("delivery_month")

        # Get all trade dates for this root
        all_dates = sorted(root_settles["trade_date"].unique())

        # For each trade date, determine the active (front) contract:
        # it's the nearest-expiry contract whose roll_off_date has NOT yet passed
        active_records = []
        for td in all_dates:
            # Contracts that haven't rolled off yet
            eligible = roll_df[roll_df["roll_off_date"] > td]
            if eligible.empty:
                # All contracts have rolled off — use the latest one available
                eligible = roll_df.tail(1)

            # Pick the one with the earliest delivery_month (= front contract)
            front = eligible.iloc[0]
            active_records.append({
                "trade_date": td,
                "active_symbol": front["raw_symbol"],
            })

        cal = pd.DataFrame(active_records)

        # Identify roll dates (where active_symbol changes)
        cal["prev_symbol"] = cal["active_symbol"].shift(1)
        cal["is_roll"] = (cal["active_symbol"] != cal["prev_symbol"]) & cal["prev_symbol"].notna()

        # Filter: only keep dates where the active contract actually has a settlement
        valid_settles = set(
            root_settles[["raw_symbol", "trade_date"]].itertuples(index=False, name=None)
        )
        cal["has_settle"] = cal.apply(
            lambda r: (r["active_symbol"], r["trade_date"]) in valid_settles, axis=1
        )
        n_missing = (~cal["has_settle"]).sum()
        if n_missing > 0:
            print(f"  {root}: {n_missing}/{len(cal)} dates missing settlement for front contract")

        cal = cal[cal["has_settle"]].copy()
        cal = cal.drop(columns=["prev_symbol", "has_settle"])

        # Recompute is_roll after filtering
        cal["prev_symbol"] = cal["active_symbol"].shift(1)
        cal["is_roll"] = (cal["active_symbol"] != cal["prev_symbol"]) & cal["prev_symbol"].notna()
        from_syms = cal.loc[cal["is_roll"], "prev_symbol"].tolist()
        to_syms = cal.loc[cal["is_roll"], "active_symbol"].tolist()
        roll_dates_list = cal.loc[cal["is_roll"], "trade_date"].tolist()
        cal = cal.drop(columns=["prev_symbol"])

        n_rolls = cal["is_roll"].sum()
        print(f"  {root}: {len(cal)} dates, {n_rolls} rolls, "
              f"{cal['trade_date'].min()} -> {cal['trade_date'].max()}")

        calendars[root] = {
            "calendar": cal,
            "rolls": list(zip(roll_dates_list, from_syms, to_syms)),
        }

    return calendars


# ---------------------------------------------------------------------------
# Step 4: Construct continuous series
# ---------------------------------------------------------------------------

def build_continuous_series(
    calendars: dict,
    settles_df: pd.DataFrame,
    is_start: str = "2011-07-01",
    oos_start: str = "2023-01-01",
) -> dict[str, pd.DataFrame]:
    """Build signal (ratio-adjusted) and PnL (actual settle-to-settle) series."""
    print("\nConstructing continuous series...")

    # Build a fast lookup: (raw_symbol, trade_date) -> settlement
    settle_lookup = dict(zip(
        zip(settles_df["raw_symbol"], settles_df["trade_date"]),
        settles_df["settlement"],
    ))

    results = {}

    for root in ROOTS:
        if root not in calendars:
            print(f"  {root}: skipped (no calendar)")
            continue

        cal = calendars[root]["calendar"].copy()
        multiplier = MULTIPLIERS[root]

        # Attach raw settlement
        cal["raw_settle"] = cal.apply(
            lambda r: settle_lookup.get((r["active_symbol"], r["trade_date"]), np.nan),
            axis=1,
        )
        cal = cal.dropna(subset=["raw_settle"]).reset_index(drop=True)

        # --- Signal series: ratio (proportional) back-adjustment ---
        # Work backwards from the end. At each roll, scale prior history by
        # (new_price / old_price) so returns are continuous.
        signal_prices = cal["raw_settle"].values.copy().astype(float)
        is_roll = cal["is_roll"].values

        # Compute ratio adjustments at each roll point
        # At a roll date, the active_symbol has CHANGED to the new contract.
        # We need the old contract's settlement on the previous day and the
        # new contract's settlement on the roll date.
        cum_ratio = 1.0
        ratios = np.ones(len(cal))

        # Walk backwards from end
        for i in range(len(cal) - 1, 0, -1):
            if is_roll[i]:
                old_sym = cal.iloc[i - 1]["active_symbol"]
                new_sym = cal.iloc[i]["active_symbol"]
                roll_td = cal.iloc[i]["trade_date"]
                prev_td = cal.iloc[i - 1]["trade_date"]

                new_price = settle_lookup.get((new_sym, roll_td), np.nan)
                old_price = settle_lookup.get((old_sym, roll_td), None)
                if old_price is None:
                    old_price = settle_lookup.get((old_sym, prev_td), np.nan)

                if not np.isnan(new_price) and not np.isnan(old_price) and old_price > 0:
                    ratio = new_price / old_price
                    cum_ratio *= ratio

            ratios[i - 1] = cum_ratio

        # Apply: signal_price = raw_settle * ratio_factor for each point
        # But ratio_factor for point i means "multiply by cum_ratio at i"
        # Actually, the standard approach: anchor at the end (ratio=1.0 for
        # the most recent data). Walk backwards, at each roll multiply
        # the cumulative ratio.
        #
        # signal_price[i] = raw_settle[i] * ratios[i]
        # But we accumulated ratios such that ratios[-1] = 1.0 and earlier
        # indices have the cumulative product.
        signal_prices = cal["raw_settle"].values * ratios

        cal["signal_price"] = signal_prices

        # --- PnL series: actual settle-to-settle ---
        pnl_daily = np.zeros(len(cal))
        for i in range(1, len(cal)):
            curr_sym = cal.iloc[i]["active_symbol"]
            prev_sym = cal.iloc[i - 1]["active_symbol"]
            curr_td = cal.iloc[i]["trade_date"]
            prev_td = cal.iloc[i - 1]["trade_date"]

            if is_roll[i]:
                # Roll day: PnL from the OLD contract's final day
                old_settle_today = settle_lookup.get((prev_sym, curr_td), None)
                old_settle_yesterday = settle_lookup.get((prev_sym, prev_td), np.nan)

                if old_settle_today is not None and not np.isnan(old_settle_yesterday):
                    pnl_daily[i] = (old_settle_today - old_settle_yesterday) * multiplier
                else:
                    # Fallback: use new contract only
                    new_today = settle_lookup.get((curr_sym, curr_td), np.nan)
                    new_yest = settle_lookup.get((curr_sym, prev_td), np.nan)
                    if not np.isnan(new_today) and not np.isnan(new_yest):
                        pnl_daily[i] = (new_today - new_yest) * multiplier
            else:
                curr_settle = settle_lookup.get((curr_sym, curr_td), np.nan)
                prev_settle = settle_lookup.get((curr_sym, prev_td), np.nan)
                if not np.isnan(curr_settle) and not np.isnan(prev_settle):
                    pnl_daily[i] = (curr_settle - prev_settle) * multiplier

        cal["pnl_daily_usd"] = pnl_daily

        # Tag sample periods
        cal["sample"] = "WARMUP"
        cal.loc[cal["trade_date"] >= is_start, "sample"] = "IS"
        cal.loc[cal["trade_date"] >= oos_start, "sample"] = "OOS"

        # Verify signal series has no negative prices
        neg_count = (cal["signal_price"] <= 0).sum()
        if neg_count > 0:
            print(f"  WARNING {root}: {neg_count} negative/zero signal prices!")

        print(f"  {root}: {len(cal)} rows, "
              f"signal range [{cal['signal_price'].min():.4f}, {cal['signal_price'].max():.4f}], "
              f"IS PnL sum ${cal.loc[cal['sample']=='IS', 'pnl_daily_usd'].sum():,.0f}")

        results[root] = cal

    return results


# ---------------------------------------------------------------------------
# Step 4b: Collapse CME Sunday pseudo-settlements (CANONICAL build-layer step)
# ---------------------------------------------------------------------------

def collapse_sundays(continuous: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Fold CME Sunday-evening pseudo-settlements into the next trading day.

    Globex posts ~51 Sunday preliminary settles/yr (sub-tick noise). Left in,
    they inflate the series to ~305 obs/yr, which makes a 252-row lookback ~10
    months instead of 12 and corrupts sqrt(252) annualization. We fold each
    Sunday row's daily PnL into the next surviving (non-Sunday) row and drop the
    Sunday row, yielding a standard ~252-obs/yr daily series.

    This is the SINGLE canonical place this cleaning happens, so IS, the
    parameter sweep, and OOS are all constructed from an identical Sunday-free
    series. It was previously done inline in step4_backtest.load_clean; that
    duplicate path has been removed and step4 now reads the already-clean DB.

    is_roll is recomputed from active_symbol changes AFTER the drop, which
    correctly relocates the handful of rolls that landed on a (now-dropped)
    Sunday onto the next trading day. PnL continuity across such a roll is
    preserved by the fold: Monday = old(Fri->Sun) + new(Sun->Mon).
    """
    out = {}
    print("\nCollapsing Sunday pseudo-settlements (canonical build-layer step):")
    for root, df in continuous.items():
        df = df.sort_values("trade_date").reset_index(drop=True)
        n_sun = 0
        n_sun_rolls = 0
        carry = 0.0
        keep = []
        for _, r in df.iterrows():
            dow = pd.Timestamp(r["trade_date"]).dayofweek
            pnl = r["pnl_daily_usd"]
            pnl = 0.0 if pd.isna(pnl) else float(pnl)
            if dow == 6:  # Sunday
                n_sun += 1
                if int(r["is_roll"]) == 1:
                    n_sun_rolls += 1
                carry += pnl
                continue
            r = r.copy()
            r["pnl_daily_usd"] = pnl + carry
            carry = 0.0
            keep.append(r)

        clean = pd.DataFrame(keep).reset_index(drop=True)
        # Recompute is_roll from active_symbol changes (relocates Sunday-rolls).
        prev = clean["active_symbol"].shift(1)
        clean["is_roll"] = ((clean["active_symbol"] != prev) & prev.notna()).astype(int)

        out[root] = clean
        print(f"  {root}: {n_sun} Sundays folded ({n_sun_rolls} were rolls) "
              f"-> {len(clean)} clean rows, {int(clean['is_roll'].sum())} rolls")
    return out


def recollapse_persisted_db() -> None:
    """One-time migration: apply collapse_sundays to the ALREADY-persisted series.

    Equivalent to the collapse step a full rebuild would run, but operates on the
    blessed pre-collapse settlements in futures_research.db rather than re-parsing
    the raw Databento files -- so the in-sample numbers reviewed on Desktop are
    preserved exactly while the Sunday cleaning is moved out of step4 and into the
    canonical layer. Safe to re-run (idempotent: a clean series has no Sundays).
    """
    print(f"Re-collapsing persisted series in {RESEARCH_DB} ...")
    conn = sqlite3.connect(RESEARCH_DB)
    continuous = {}
    for root in ROOTS:
        df = pd.read_sql(
            "SELECT trade_date, active_symbol, raw_settle, signal_price, "
            "pnl_daily_usd, is_roll, sample FROM futures_continuous "
            "WHERE root = ? ORDER BY trade_date",
            conn, params=(root,),
        )
        continuous[root] = df

    collapsed = collapse_sundays(continuous)

    cont_frames = []
    for root, df in collapsed.items():
        frame = df[["trade_date", "active_symbol", "raw_settle", "signal_price",
                    "pnl_daily_usd", "is_roll", "sample"]].copy()
        frame["root"] = root
        frame["is_roll"] = frame["is_roll"].astype(int)
        cont_frames.append(frame)
    cont_df = pd.concat(cont_frames, ignore_index=True)
    cont_df = cont_df[["root", "trade_date", "active_symbol", "raw_settle",
                       "signal_price", "pnl_daily_usd", "is_roll", "sample"]]
    cont_df.to_sql("futures_continuous", conn, if_exists="replace", index=False)
    conn.executescript(
        "CREATE INDEX IF NOT EXISTS idx_continuous_sample "
        "ON futures_continuous(root, sample, trade_date);"
    )
    conn.close()
    print(f"  futures_continuous rewritten: {len(cont_df)} rows (Sunday-free).")


# ---------------------------------------------------------------------------
# Step 5: Persist to SQLite
# ---------------------------------------------------------------------------

def persist_to_sqlite(
    contracts_df: pd.DataFrame,
    settles_df: pd.DataFrame,
    calendars: dict,
    continuous: dict[str, pd.DataFrame],
) -> None:
    """Write all data to the research SQLite database."""
    print(f"\nPersisting to {RESEARCH_DB}...")
    RESEARCH_DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(RESEARCH_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)

    # Contracts
    contracts_for_db = contracts_df[
        ["raw_symbol", "root", "expiration", "delivery_month", "multiplier", "tick_size"]
    ].copy()
    contracts_for_db.to_sql(
        "futures_contracts", conn, if_exists="replace", index=False,
    )
    print(f"  futures_contracts: {len(contracts_for_db)} rows")

    # Settlements
    settles_for_db = settles_df[["raw_symbol", "trade_date", "settlement"]].copy()
    settles_for_db.to_sql(
        "futures_settlements", conn, if_exists="replace", index=False,
    )
    print(f"  futures_settlements: {len(settles_for_db)} rows")

    # Roll calendar
    all_rolls = []
    for root, cal_data in calendars.items():
        for roll_date, from_sym, to_sym in cal_data["rolls"]:
            all_rolls.append({
                "root": root,
                "roll_date": roll_date,
                "from_symbol": from_sym,
                "to_symbol": to_sym,
            })
    rolls_df = pd.DataFrame(all_rolls)
    if not rolls_df.empty:
        rolls_df.to_sql("futures_roll_calendar", conn, if_exists="replace", index=False)
    print(f"  futures_roll_calendar: {len(rolls_df)} rows")

    # Continuous series
    cont_frames = []
    for root, df in continuous.items():
        frame = df[["trade_date", "active_symbol", "raw_settle",
                     "signal_price", "pnl_daily_usd", "is_roll", "sample"]].copy()
        frame["root"] = root
        frame["is_roll"] = frame["is_roll"].astype(int)
        cont_frames.append(frame)
    cont_df = pd.concat(cont_frames, ignore_index=True)
    cont_df = cont_df[["root", "trade_date", "active_symbol", "raw_settle",
                        "signal_price", "pnl_daily_usd", "is_roll", "sample"]]
    cont_df.to_sql("futures_continuous", conn, if_exists="replace", index=False)
    print(f"  futures_continuous: {len(cont_df)} rows")

    # Recreate indexes
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_settlements_root
            ON futures_settlements(raw_symbol);
        CREATE INDEX IF NOT EXISTS idx_continuous_sample
            ON futures_continuous(root, sample, trade_date);
    """)

    conn.close()
    print("  Done.")


# ---------------------------------------------------------------------------
# Step 6: Validation
# ---------------------------------------------------------------------------

def validate(
    continuous: dict[str, pd.DataFrame],
    settles_df: pd.DataFrame,
    calendars: dict,
) -> bool:
    """Run validation checks on the constructed series."""
    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)
    ok = True

    settle_lookup = dict(zip(
        zip(settles_df["raw_symbol"], settles_df["trade_date"]),
        settles_df["settlement"],
    ))

    for root, df in continuous.items():
        print(f"\n--- {root} ---")

        # 1. No negative signal prices
        neg = (df["signal_price"] <= 0).sum()
        if neg > 0:
            print(f"  FAIL: {neg} negative/zero signal prices")
            ok = False
        else:
            print(f"  OK: No negative signal prices")

        # 2. Check roll dates land where expected
        rolls = df[df["is_roll"]]
        print(f"  Rolls: {len(rolls)} total")
        if len(rolls) > 0:
            print(f"  First 3 rolls:")
            for _, r in rolls.head(3).iterrows():
                print(f"    {r['trade_date']}: -> {r['active_symbol']}")

        # 3. Hand-check 2-3 rolls: verify ratio adjustment
        for _, r in rolls.head(3).iterrows():
            roll_idx = df.index[df["trade_date"] == r["trade_date"]][0]
            if roll_idx == 0:
                continue
            prev_row = df.iloc[roll_idx - 1]
            new_sym = r["active_symbol"]
            old_sym = prev_row["active_symbol"]
            roll_td = r["trade_date"]
            prev_td = prev_row["trade_date"]

            old_settle_roll = settle_lookup.get((old_sym, roll_td))
            new_settle_roll = settle_lookup.get((new_sym, roll_td))
            old_settle_prev = settle_lookup.get((old_sym, prev_td))

            if old_settle_roll and new_settle_roll and old_settle_prev:
                expected_ratio = new_settle_roll / old_settle_roll
                # Signal return across roll should equal old contract's return
                signal_ret = r["signal_price"] / prev_row["signal_price"]
                old_ret = old_settle_roll / old_settle_prev
                if abs(signal_ret - old_ret) > 1e-8:
                    print(f"    FAIL roll {roll_td}: signal_ret={signal_ret:.8f} vs "
                          f"old_ret={old_ret:.8f}")
                    ok = False
                else:
                    print(f"    OK roll {roll_td}: {old_sym}->{new_sym}, "
                          f"ratio={expected_ratio:.6f}, return continuity verified")

        # 4. Anchor-invariance test: ratio-adjusted returns are independent of anchor point
        if len(df) > 100:
            raw = df["raw_settle"].values
            is_roll_arr = df["is_roll"].values
            active_syms = df["active_symbol"].values
            trade_dates = df["trade_date"].values

            def build_ratio_adjusted(anchor_idx):
                """Build ratio-adjusted series anchored at anchor_idx."""
                prices = raw.copy().astype(float)
                ratios = np.ones(len(prices))
                cum = 1.0
                for i in range(anchor_idx - 1, -1, -1):
                    if is_roll_arr[i + 1]:
                        old_sym = active_syms[i]
                        new_sym = active_syms[i + 1]
                        td = trade_dates[i + 1]
                        new_p = settle_lookup.get((new_sym, td), np.nan)
                        old_p = settle_lookup.get((old_sym, td), None)
                        if old_p is None:
                            old_p = settle_lookup.get((old_sym, trade_dates[i]), np.nan)
                        if not np.isnan(new_p) and not np.isnan(old_p) and old_p > 0:
                            cum *= new_p / old_p
                    ratios[i] = cum
                # Forward from anchor
                cum = 1.0
                for i in range(anchor_idx + 1, len(prices)):
                    if is_roll_arr[i]:
                        old_sym = active_syms[i - 1]
                        new_sym = active_syms[i]
                        td = trade_dates[i]
                        new_p = settle_lookup.get((new_sym, td), np.nan)
                        old_p = settle_lookup.get((old_sym, td), None)
                        if old_p is None:
                            old_p = settle_lookup.get((old_sym, trade_dates[i - 1]), np.nan)
                        if not np.isnan(new_p) and not np.isnan(old_p) and old_p > 0:
                            cum *= old_p / new_p
                    ratios[i] = cum
                return prices * ratios

            # Build from two different anchors
            anchor_a = len(df) - 1  # end
            anchor_b = len(df) // 2  # middle

            series_a = build_ratio_adjusted(anchor_a)
            series_b = build_ratio_adjusted(anchor_b)

            # IS period returns should be identical
            is_mask = df["sample"].values == "IS"
            if is_mask.sum() > 1:
                is_indices = np.where(is_mask)[0]
                rets_a = np.diff(series_a[is_indices]) / series_a[is_indices[:-1]]
                rets_b = np.diff(series_b[is_indices]) / series_b[is_indices[:-1]]
                max_diff = np.max(np.abs(rets_a - rets_b))
                if max_diff > 1e-10:
                    print(f"  FAIL: Anchor invariance failed, max return diff = {max_diff:.2e}")
                    ok = False
                else:
                    print(f"  OK: Anchor-invariant (max return diff = {max_diff:.2e})")

    return ok


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(
    contracts_df: pd.DataFrame,
    continuous: dict[str, pd.DataFrame],
    calendars: dict,
) -> None:
    """Print summary report."""
    print("\n" + "=" * 60)
    print("REPORT")
    print("=" * 60)

    for root in ROOTS:
        if root not in continuous:
            print(f"\n{root}: NO DATA")
            continue

        df = continuous[root]
        root_contracts = contracts_df[contracts_df["root"] == root]
        n_rolls = df["is_roll"].sum()

        is_data = df[df["sample"] == "IS"]
        oos_data = df[df["sample"] == "OOS"]

        print(f"\n--- {root} (multiplier=${MULTIPLIERS[root]:,.0f}) ---")
        print(f"  Contracts: {len(root_contracts)}")
        print(f"  History span: {df['trade_date'].min()} -> {df['trade_date'].max()}")
        print(f"  Total dates: {len(df)}")
        print(f"  Rolls: {n_rolls}")
        print(f"  IS dates: {len(is_data)} ({is_data['trade_date'].min() if len(is_data) else 'N/A'}"
              f" -> {is_data['trade_date'].max() if len(is_data) else 'N/A'})")
        print(f"  OOS dates: {len(oos_data)} (QUARANTINED)")

        # Show head/tail of signal series (IS only)
        if len(is_data) > 0:
            print(f"\n  Signal series (head, IS):")
            for _, r in is_data.head(3).iterrows():
                print(f"    {r['trade_date']}  {r['active_symbol']:>8s}  "
                      f"raw={r['raw_settle']:>10.4f}  signal={r['signal_price']:>10.4f}")
            print(f"  Signal series (tail, IS):")
            for _, r in is_data.tail(3).iterrows():
                print(f"    {r['trade_date']}  {r['active_symbol']:>8s}  "
                      f"raw={r['raw_settle']:>10.4f}  signal={r['signal_price']:>10.4f}")

        # PnL series sample (IS only)
        if len(is_data) > 0:
            print(f"\n  PnL series (head, IS):")
            for _, r in is_data.head(3).iterrows():
                print(f"    {r['trade_date']}  {r['active_symbol']:>8s}  "
                      f"settle={r['raw_settle']:>10.4f}  pnl=${r['pnl_daily_usd']:>10.2f}"
                      f"{'  ROLL' if r['is_roll'] else ''}")
            print(f"  PnL series (tail, IS):")
            for _, r in is_data.tail(3).iterrows():
                print(f"    {r['trade_date']}  {r['active_symbol']:>8s}  "
                      f"settle={r['raw_settle']:>10.4f}  pnl=${r['pnl_daily_usd']:>10.2f}"
                      f"{'  ROLL' if r['is_roll'] else ''}")

        # Roll schedule sample
        if root in calendars:
            roll_list = calendars[root]["rolls"]
            if roll_list:
                print(f"\n  Roll schedule (first 5):")
                for rd, fs, ts in roll_list[:5]:
                    print(f"    {rd}: {fs} -> {ts}")
                if len(roll_list) > 5:
                    print(f"  ... and {len(roll_list) - 5} more rolls")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Continuous Contract Builder — Diversified Trend Futures")
    print("=" * 60)
    t_start = time.time()

    # Step 1: Definitions
    contracts_df = load_definitions()

    # Step 2: Settlements
    settles_df = load_settlements(contracts_df)

    # Step 3: Roll calendar
    calendars = build_roll_calendar(contracts_df, settles_df)

    # Step 4: Continuous series (raw, still carrying Sunday pseudo-settlements)
    continuous_raw = build_continuous_series(calendars, settles_df)

    # Step 6: Validate the RAW build (ratio adjustment / roll continuity) before cleaning
    all_ok = validate(continuous_raw, settles_df, calendars)

    # Step 4b: Canonical Sunday collapse (single source of truth for IS/sweep/OOS)
    continuous = collapse_sundays(continuous_raw)

    # Step 5: Persist the cleaned series
    persist_to_sqlite(contracts_df, settles_df, calendars, continuous)

    # Report
    print_report(contracts_df, continuous, calendars)

    elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"Total time: {elapsed:.1f}s")
    print(f"Validation: {'ALL PASSED' if all_ok else 'FAILURES DETECTED'}")
    print(f"Database: {RESEARCH_DB}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    if "--recollapse" in sys.argv:
        # Fast migration: collapse Sundays in the already-built DB (no reparse).
        recollapse_persisted_db()
    else:
        main()
