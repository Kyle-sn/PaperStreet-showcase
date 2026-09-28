"""
Step 2 data loader for the petroleum-status-drift strategy (crude / MCL):
pulls the EIA Weekly Petroleum Status Report crude-inventory series and
builds a point-in-time release-date calendar.

Two things are acquired here, kept as two clearly separate columns because
they answer different questions:

  - `period`       — the week-ending date the inventory number is *as of*
                      (EIA's own field name is "period"; always a Friday).
  - `release_date` — the calendar date the number was actually *published*.
                      This is what the event study must anchor to. Naively
                      assuming "every Wednesday" mis-dates every holiday
                      week (see RESEARCH_WORKFLOW_petroleum_status_drift.md
                      Step 2 / OD#2's point-in-time-calendar requirement).

Source
------
EIA Open Data v2 (https://www.eia.gov/opendata/), series WCESTUS1 —
"U.S. Ending Stocks excluding SPR of Crude Oil (Thousand Barrels)", weekly.
This is the commercial-crude number the report's headline draw/build figure
is computed from. Free, no auth beyond an API key (DEMO_KEY works but is
rate-limited; set EIA_API_KEY for real use — register at
https://www.eia.gov/opendata/register.php).

Release-date derivation
------------------------
EIA does NOT publish a machine-readable historical archive of actual
release timestamps (checked: the v2 API returns only `period`, no release
field; ir.eia.gov and eia.gov/petroleum/weekly/includes/schedule.php only
list the *current* holiday-shift exception table, presently covering
2024-2025). So release_date here is DERIVED, not scraped, from the
documented publication rule, cross-checked against the one primary source
that does exist (the schedule.php exception table for 2024-2025):

  1. Normal release = the Wednesday following the Friday period-end
     (period_end + 5 calendar days).
  2. If that Wednesday is itself a federal holiday, OR the Monday of that
     same release week is a federal holiday, the release shifts to
     Thursday (+1 day). Confirmed against all 13 non-Christmas exceptions
     in the 2024-2025 schedule.php table.
  3. Exception: when the computed Wednesday lands exactly on Christmas Day
     (Dec 25), the confirmed historical shift is +2 days (Friday), not +1
     — see the Dec 20 2024 -> Dec 27 2024 case. Hardcoded as an override.

Rows matching a hardcoded, source-cited confirmed exception are marked
`release_date_confidence="confirmed"`; everything else is
`"rule_derived"`. Known further irregularities NOT modeled here (flag for
Step 2 review, not fabricated as certainty):
  - Government shutdowns (e.g. Jan 2019, ~35 days) can delay a release
    independent of the holiday calendar.
  - Severe-weather data-collection disruption (e.g. Hurricane Harvey,
    Aug/Sep 2017) delayed at least one report.
  - Pre-2021 Juneteenth is correctly NOT treated as a federal holiday
    (pandas' USFederalHolidayCalendar rule starts 2021-06-18).
These are rare (a handful of weeks total across 40+ years) and do not
change the G0 event-depth count, but a signal built on this calendar
should spot-check its date-of-surprise window doesn't straddle one.

Usage
-----
    python -m research.killed.petroleum_status_drift.eia_fetch fetch
    python -m research.killed.petroleum_status_drift.eia_fetch validate
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

_HERE = Path(__file__).parent
_RAW_DIR = _HERE / "data" / "raw"
_CSV_PATH = _HERE / "data" / "eia_weekly_crude_stocks.csv"

EIA_SERIES = "WCESTUS1"
EIA_BASE_URL = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
_PAGE_SIZE = 5000  # EIA v2 hard cap per request

# -- Confirmed exceptions, source: eia.gov/petroleum/weekly/includes/schedule.php
#    (fetched 2026-08-24; page covers 2024-2025 only — the only primary-source
#    exception table EIA publishes; no historical archive exists further back).
#    period_end (week-ending Friday, ISO) -> confirmed release_date (ISO)
CONFIRMED_EXCEPTIONS: dict[str, str] = {
    "2024-01-12": "2024-01-18",  # MLK Day (Mon Jan 15)
    "2024-02-16": "2024-02-22",  # Presidents Day (Mon Feb 19)
    "2024-05-24": "2024-05-30",  # Memorial Day (Mon May 27)
    "2024-06-14": "2024-06-20",  # Juneteenth (Wed Jun 19 itself)
    "2024-08-30": "2024-09-05",  # Labor Day (Mon Sep 2)
    "2024-10-11": "2024-10-17",  # Columbus Day (Mon Oct 14)
    "2024-11-08": "2024-11-14",  # Veterans Day (Mon Nov 11)
    "2024-12-20": "2024-12-27",  # Christmas Day (Wed Dec 25 itself) -- +2, not +1
    "2024-12-27": "2025-01-02",  # New Year's Day (Wed Jan 1 itself)
    "2025-01-17": "2025-01-23",  # MLK Day / Inauguration Day (Mon Jan 20)
    "2025-02-14": "2025-02-20",  # Presidents Day (Mon Feb 17)
    "2025-05-23": "2025-05-29",  # Memorial Day (Mon May 26)
    "2025-08-29": "2025-09-04",  # Labor Day (Mon Sep 1)
    "2025-10-10": "2025-10-16",  # Columbus Day (Mon Oct 13)
}

_HOLIDAY_CAL = USFederalHolidayCalendar()


def _federal_holidays(start: str, end: str) -> set:
    return set(_HOLIDAY_CAL.holidays(start=start, end=end).normalize())


def infer_release_date(period_end: pd.Timestamp, holidays: set) -> tuple[pd.Timestamp, str, str]:
    """Derive (release_date, note, confidence) for one week-ending date.

    See module docstring for the rule and its sourcing.
    """
    period_end = pd.Timestamp(period_end).normalize()
    key = period_end.date().isoformat()

    if key in CONFIRMED_EXCEPTIONS:
        rel = pd.Timestamp(CONFIRMED_EXCEPTIONS[key])
        note = "confirmed_exception"
        return rel, note, "confirmed"

    normal = period_end + pd.Timedelta(days=5)  # following Wednesday
    monday_of_week = normal - pd.Timedelta(days=2)

    if normal.month == 12 and normal.day == 25:
        # Christmas-Day-on-release-Wednesday: confirmed historical shift is
        # +2 (Friday), not the usual +1 -- see CONFIRMED_EXCEPTIONS 2024-12-20.
        return normal + pd.Timedelta(days=2), "rule_christmas_override", "rule_derived"

    if normal in holidays:
        return normal + pd.Timedelta(days=1), "rule_wednesday_is_holiday", "rule_derived"

    if monday_of_week in holidays:
        return normal + pd.Timedelta(days=1), "rule_monday_is_holiday", "rule_derived"

    return normal, "rule_normal", "rule_derived"


def _fetch_page(api_key: str, offset: int) -> dict:
    params = (
        f"api_key={api_key}&frequency=weekly&data[0]=value"
        f"&facets[series][]={EIA_SERIES}"
        f"&sort[0][column]=period&sort[0][direction]=asc"
        f"&offset={offset}&length={_PAGE_SIZE}"
    )
    url = f"{EIA_BASE_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "PaperStreet-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_all(api_key: str | None = None) -> pd.DataFrame:
    """Pull the full WCESTUS1 history from EIA, archive the raw responses,
    and return a cleaned DataFrame with period, value_mbbl, release_date.
    """
    api_key = api_key or os.environ.get("EIA_API_KEY", "DEMO_KEY")
    if api_key == "DEMO_KEY":
        print("WARNING: using EIA DEMO_KEY (tight rate limit). Register a free "
              "key at https://www.eia.gov/opendata/register.php and set "
              "EIA_API_KEY for repeated/production use.")

    _RAW_DIR.mkdir(parents=True, exist_ok=True)

    all_rows = []
    offset = 0
    page_num = 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    while True:
        page_num += 1
        print(f"  fetching offset={offset} ...")
        payload = _fetch_page(api_key, offset)

        raw_path = _RAW_DIR / f"eia_wcestus1_{stamp}_p{page_num:03d}.json"
        raw_path.write_text(json.dumps(payload, indent=2))

        rows = payload["response"]["data"]
        total = int(payload["response"]["total"])
        all_rows.extend(rows)
        print(f"    got {len(rows)} rows (total available: {total})")

        offset += len(rows)
        if len(rows) < _PAGE_SIZE or offset >= total or not rows:
            break
        time.sleep(1.0)  # be polite even under DEMO_KEY's tighter limit

    if not all_rows:
        raise ValueError("EIA API returned no rows for series WCESTUS1")

    df = pd.DataFrame(all_rows)
    df["period"] = pd.to_datetime(df["period"])
    df["value_mbbl"] = df["value"].astype(float)
    df = df[["period", "value_mbbl"]].drop_duplicates(subset="period").sort_values("period")
    df = df.reset_index(drop=True)

    holidays = _federal_holidays(
        start=str(df["period"].min().year - 1),
        end=str(df["period"].max().year + 1),
    )
    inferred = df["period"].apply(lambda p: infer_release_date(p, holidays))
    df["release_date"] = inferred.apply(lambda t: t[0])
    df["release_date_note"] = inferred.apply(lambda t: t[1])
    df["release_date_confidence"] = inferred.apply(lambda t: t[2])
    df["release_weekday"] = df["release_date"].dt.day_name()

    df.to_csv(_CSV_PATH, index=False)
    print(f"\nWrote {len(df)} rows -> {_CSV_PATH}")
    print(f"Period range: {df['period'].min().date()} -> {df['period'].max().date()}")
    n_confirmed = (df["release_date_confidence"] == "confirmed").sum()
    n_shifted = (df["release_date_note"] != "rule_normal").sum()
    print(f"Confirmed release dates: {n_confirmed} / {len(df)}")
    print(f"Non-standard (shifted) release dates: {n_shifted} / {len(df)}")
    return df


def validate() -> bool:
    """Sanity-check the confirmed-exception table replays correctly through
    infer_release_date, and print the full non-Wednesday release list for
    manual eyeballing.
    """
    holidays = _federal_holidays(start="2020-01-01", end="2027-01-01")
    ok = True
    for period_str, expected_str in CONFIRMED_EXCEPTIONS.items():
        got, note, conf = infer_release_date(pd.Timestamp(period_str), holidays)
        expected = pd.Timestamp(expected_str)
        status = "OK" if got == expected else "MISMATCH"
        if got != expected:
            ok = False
        print(f"  {period_str} -> got {got.date()} ({note}), expected {expected.date()}  [{status}]")

    if _CSV_PATH.exists():
        df = pd.read_csv(_CSV_PATH, parse_dates=["period", "release_date"])
        non_wed = df[df["release_date"].dt.day_name() != "Wednesday"]
        print(f"\n{len(non_wed)} non-Wednesday releases in cached data "
              f"({len(df)} total rows):")
        print(non_wed[["period", "release_date", "release_weekday",
                        "release_date_note", "release_date_confidence"]].to_string(index=False))
    else:
        print(f"\nNo cached data at {_CSV_PATH} yet -- run `fetch` first.")

    print(f"\n{'PASS' if ok else 'FAIL'}: confirmed-exception replay")
    return ok


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("fetch", "validate"):
        print("Usage: python -m research.killed.petroleum_status_drift.eia_fetch <fetch|validate>")
        sys.exit(1)

    if sys.argv[1] == "fetch":
        fetch_all()
    else:
        ok = validate()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
