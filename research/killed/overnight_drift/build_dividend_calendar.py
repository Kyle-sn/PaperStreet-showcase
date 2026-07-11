"""
build_dividend_calendar.py

Derive SPY's ex-dividend calendar from the IBKR daily bars already cached in
``market_data_bars``, and write it to ``spy_dividends.csv`` in this package.

Why derive instead of fetch an external table
----------------------------------------------
Per the Step-2 handoff the calendar is sourced from IBKR (same vendor as the price
data, so ex-dates align to the exact trading days the bars use — no cross-source
alignment burden). We do not have a clean IBKR *corporate-actions* feed wired in,
but we already cache two IBKR daily series for SPY:

  * TRADES         -> split-adjusted **price** return
  * ADJUSTED_LAST  -> split + dividend adjusted **total** return

On a normal day both series have the same daily return. On an **ex-dividend**
morning the total-return series adds the dividend back, so the gap between the two
daily returns, scaled by the prior close, isolates that day's cash dividend:

    div(ex) = close(ex-1) * ( adj_ret(ex) - px_ret(ex) )

The date that carries a non-zero gap **is** the ex-dividend date (the morning the
price gaps down by the dividend), which is exactly the alignment a later EDA needs
to add the dividend back to the overnight return.

Coverage limitation (documented, accepted)
-------------------------------------------
IBKR's ADJUSTED_LAST for SPY only encodes dividend adjustments from ~2006 onward
(across 1997-2005 the TRADES/ADJUSTED_LAST ratio is frozen, i.e. no dividend
adjustment is applied). So this derived calendar is:

  * clean and complete (4/yr) from 2006-03 onward,
  * sparse/partial for 1993-1996,
  * EMPTY for 1997-2005.

This was an explicit decision (do not pull an external pre-2006 source for v1).
The consequence for Step 3: ex-dividend overnight returns in 1997-2005 cannot be
corrected and will carry the spurious ~-20-30 bps ex-div artifact on ~4 mornings/yr
in that window. Quantify/flag it in the EDA; an external pre-2006 calendar is a
later optional extension only.

Noise floor
-----------
ADJUSTED_LAST is stored to 2 decimals, so the implied dividend has a few-bps
rounding hash on every day. Real SPY dividends are all > $0.10; the rounding noise
is all < $0.02. ``MIN_DIVIDEND`` sits in the clean gap between them. Only positive
gaps are kept (a dividend is positive; the lone negative is a one-day boundary
smear of the most recent ex-date and is dropped by the positive filter).

Run
---
    python -m research.killed.overnight_drift.build_dividend_calendar

Reads the DB cache only (no TWS needed once SPY TRADES + ADJUSTED_LAST are cached).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from database import market_data as mdb

SYMBOL = "SPY"
BAR_SIZE = "1 day"
# Clean gap between rounding noise (< $0.02) and the smallest real SPY dividend
# (~$0.16). Real dividends are insensitive to the exact cutoff anywhere in
# [$0.02, $0.10]; the count is identical across that range.
MIN_DIVIDEND = 0.02

_OUT = Path(__file__).resolve().parent / "spy_dividends.csv"


def derive_calendar() -> pd.DataFrame:
    """Return a DataFrame [ex_date, amount] of SPY ex-dividends from the IBKR cache."""
    tr = mdb.get_bars(SYMBOL, bar_size=BAR_SIZE, what_to_show="TRADES")
    adj = mdb.get_bars(SYMBOL, bar_size=BAR_SIZE, what_to_show="ADJUSTED_LAST")
    if tr is None or adj is None:
        raise ValueError(
            "SPY TRADES and ADJUSTED_LAST daily bars must both be cached first "
            "(run a deep fetch of each via research.session.Session)."
        )

    df = tr[["close"]].rename(columns={"close": "px"}).join(
        adj[["close"]].rename(columns={"close": "adj"}), how="inner"
    )
    # close(ex-1) * (adj_ret - px_ret) isolates the dividend on the ex-date.
    implied = df["px"].shift(1) * (df["adj"].pct_change() - df["px"].pct_change())

    cal = implied[implied > MIN_DIVIDEND].round(4)
    out = cal.reset_index()
    out.columns = ["ex_date", "amount"]
    out["ex_date"] = pd.to_datetime(out["ex_date"]).dt.strftime("%Y-%m-%d")
    return out


def main() -> None:
    cal = derive_calendar()
    cal.to_csv(_OUT, index=False)
    by_year = cal.assign(year=cal["ex_date"].str[:4]).groupby("year").size()
    print(f"Wrote {len(cal)} ex-dividend dates to {_OUT}")
    print(f"  range: {cal['ex_date'].iloc[0]} -> {cal['ex_date'].iloc[-1]}")
    print(f"  total cash/share summed: ${cal['amount'].sum():.2f}")
    print("  per-year count:")
    print(by_year.to_string())


if __name__ == "__main__":
    main()
