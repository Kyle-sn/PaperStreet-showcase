"""
IBKR data-depth probe for the diversified-trend locked basket.

Connects to TWS and probes historical daily data availability for each of the
8 locked instruments (ES, ZN, 6E, 6J, 6A, CL, GC, HG).

Two probes per instrument:
  1. CONTFUT (continuous futures) with max duration — quickest depth check.
  2. An individual expired FUT contract from early 2005 — checks whether raw
     per-month data exists that far back even if CONTFUT is shorter.

Reports earliest/latest dates, bar counts, and gap vs the 2005 target.

Usage (requires TWS running on port 7497):
    python -m research.killed.diversified_trend.ibkr_data_probe
"""

import time

import pandas as pd
from ibapi.contract import Contract

from research.session import Session
from utils.log_config import setup_logger

logger = setup_logger(__name__)

# -- Locked basket: 8 instruments -----------------------------------------
#
# (display_name, ibkr_symbol, exchange, currency, early_contract_month)
#
# ibkr_symbol: IBKR's root symbol (FX futures use the currency, not the
#   CME ticker — e.g. "EUR" not "6E").
# early_contract_month: a specific YYYYMM to probe for 2005-era data via
#   individual FUT with includeExpired=True. Chosen from each product's
#   contract cycle near early 2005.
BASKET = [
    ("ES",  "ES",  "CME",   "USD", "200503"),   # quarterly: H M U Z
    ("ZN",  "ZN",  "CBOT",  "USD", "200503"),   # quarterly: H M U Z
    ("6E",  "EUR", "CME",   "USD", "200503"),   # quarterly: H M U Z
    ("6J",  "JPY", "CME",   "USD", "200503"),   # quarterly: H M U Z
    ("6A",  "AUD", "CME",   "USD", "200503"),   # quarterly: H M U Z
    ("CL",  "CL",  "NYMEX", "USD", "200504"),   # monthly; Apr 2005
    ("GC",  "GC",  "COMEX", "USD", "200504"),   # even months; Apr 2005
    ("HG",  "HG",  "COMEX", "USD", "200504"),   # monthly; Apr 2005
]

TARGET_START = pd.Timestamp("2005-01-01")
_REQ_ID = 3001
_PACING_SEC = 2


def _make_contfut(symbol: str, exchange: str, currency: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "CONTFUT"
    c.exchange = exchange
    c.currency = currency
    return c


def _make_fut(symbol: str, exchange: str, currency: str,
              contract_month: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "FUT"
    c.exchange = exchange
    c.currency = currency
    c.lastTradeDateOrContractMonth = contract_month
    c.includeExpired = True
    return c


def _fetch(app, contract, duration: str = "20 Y", timeout: int = 45):
    """Fire reqHistoricalData on the IBApp and block until data or timeout."""
    app.historical_data = []
    app._historical_data_event.clear()

    app.reqHistoricalData(
        reqId=_REQ_ID,
        contract=contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting="1 day",
        whatToShow="TRADES",
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


def _probe_contfut(app, display_name, symbol, exchange, currency):
    """Try CONTFUT with decreasing durations until data arrives."""
    contract = _make_contfut(symbol, exchange, currency)
    for dur in ["20 Y", "10 Y", "5 Y", "2 Y"]:
        df = _fetch(app, contract, duration=dur)
        if df is not None and len(df) > 0:
            return df, dur
        time.sleep(_PACING_SEC)
    return None, None


def _probe_early_fut(app, symbol, exchange, currency, contract_month):
    """Try fetching daily bars for one early (expired) individual contract."""
    contract = _make_fut(symbol, exchange, currency, contract_month)
    df = _fetch(app, contract, duration="3 M", timeout=30)
    return df


def _summarize(df, label):
    if df is None or len(df) == 0:
        return {"bars": 0, "earliest": None, "latest": None}
    return {
        "bars": len(df),
        "earliest": df.index.min(),
        "latest": df.index.max(),
    }


def main():
    print("=" * 70)
    print("IBKR Data Depth Probe — Diversified Trend Basket")
    print("Target: 2005-01-01 -> present, daily bars")
    print("Instruments: ES, ZN, 6E, 6J, 6A, CL, GC, HG")
    print("=" * 70)

    results = []

    with Session() as session:
        app = session._app

        for display, sym, exch, ccy, early_month in BASKET:
            print(f"\n{'-'*60}")
            print(f"  {display}  (IBKR: {sym} on {exch})")
            print(f"{'-'*60}")

            # -- 1. CONTFUT probe --
            cf_df, cf_dur = _probe_contfut(app, display, sym, exch, ccy)
            cf = _summarize(cf_df, "CONTFUT")
            if cf["bars"]:
                print(f"  CONTFUT ({cf_dur}): {cf['bars']} bars, "
                      f"{cf['earliest'].strftime('%Y-%m-%d')} -> "
                      f"{cf['latest'].strftime('%Y-%m-%d')}")
            else:
                print(f"  CONTFUT: no data returned")

            time.sleep(_PACING_SEC)

            # -- 2. Early individual-month probe --
            ef_df = _probe_early_fut(app, sym, exch, ccy, early_month)
            ef = _summarize(ef_df, f"FUT {early_month}")
            if ef["bars"]:
                print(f"  FUT {early_month}: {ef['bars']} bars, "
                      f"{ef['earliest'].strftime('%Y-%m-%d')} -> "
                      f"{ef['latest'].strftime('%Y-%m-%d')}")
            else:
                print(f"  FUT {early_month}: no data returned")

            time.sleep(_PACING_SEC)

            # -- Assess --
            best_earliest = None
            if cf["earliest"]:
                best_earliest = cf["earliest"]
            if ef["earliest"] and (best_earliest is None
                                    or ef["earliest"] < best_earliest):
                best_earliest = ef["earliest"]

            covers = best_earliest is not None and best_earliest <= TARGET_START
            gap_days = (None if best_earliest is None
                        else max(0, (best_earliest - TARGET_START).days))

            results.append({
                "instrument": display,
                "contfut_bars": cf["bars"],
                "contfut_earliest": cf["earliest"],
                "contfut_latest": cf["latest"],
                "contfut_duration": cf_dur,
                "early_fut_month": early_month,
                "early_fut_bars": ef["bars"],
                "early_fut_earliest": ef["earliest"],
                "best_earliest": best_earliest,
                "covers_2005": covers,
                "gap_days": gap_days,
            })

    # -- Summary table --
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    hdr = (f"{'Instr':<6} {'CONTFUT earliest':<18} {'CONTFUT bars':>12} "
           f"{'Early FUT':>10} {'Best earliest':<16} {'Gap (days)':>10}")
    print(hdr)
    print("-" * 75)

    all_covered = True
    for r in results:
        cf_e = (r["contfut_earliest"].strftime("%Y-%m-%d")
                if r["contfut_earliest"] else "N/A")
        best_e = (r["best_earliest"].strftime("%Y-%m-%d")
                  if r["best_earliest"] else "N/A")
        ef_ok = "yes" if r["early_fut_bars"] else "no"
        gap = str(r["gap_days"]) if r["gap_days"] is not None else "N/A"
        print(f"{r['instrument']:<6} {cf_e:<18} {r['contfut_bars']:>12} "
              f"{ef_ok:>10} {best_e:<16} {gap:>10}")
        if not r["covers_2005"]:
            all_covered = False

    print()
    if all_covered:
        print("RESULT: IBKR covers 2005-present for all 8 instruments.")
        print("Phase 2 can proceed with IBKR as the sole data source.")
    else:
        print("RESULT: IBKR does NOT fully cover 2005-present.")
        short = [r["instrument"] for r in results if not r["covers_2005"]]
        print(f"  Shortfall instruments: {', '.join(short)}")
        print()
        print("Open Decision #3 (data vendor: CSI / Norgate / etc.) must be")
        print("resolved before Phase 2 can proceed. STOP for desktop review.")


if __name__ == "__main__":
    main()
