"""
data.py

Cache-first daily data access for the basket research, plus a one-time TWS
prewarm. Mirrors the offline-first philosophy of backtesting/data.py: read the
local SQLite cache (database.market_data) and only touch TWS to populate it.

Basis is ADJUSTED_LAST (total return) throughout — the spread, its statistics,
and the sim PnL are all computed on the same series (research = sim parity, the
same discipline docs/BACKTESTING.md prescribes for equity strategies). The
dividends-on-shorts treatment is tied to this choice; see costs.py Open
Decision #5.

Typical use
-----------
    # One-time, with TWS running (paper port 7497):
    python -m research.killed.basket_statarb.data PREWARM GOOG GOOGL DUK REGN ...

    # Thereafter, fully offline:
    from research.killed.basket_statarb import data
    closes, opens = data.load_pair_panels("GOOG", "GOOGL")
"""

from __future__ import annotations

import sys
import time

import pandas as pd

from database import market_data as _mdb
from utils.log_config import setup_logger

logger = setup_logger(__name__)

DEFAULT_BASIS = "ADJUSTED_LAST"
DEFAULT_BAR_SIZE = "1 day"

# Seconds between prewarm requests. IBKR pacing is 60 historical requests /
# 10 min (market_data/IBKR_NOTES.md); 1.0s is comfortably under it.
_PREWARM_DELAY = 1.0


# ----------------------------------------------------------------------
# Cache reads (offline)
# ----------------------------------------------------------------------

def load_daily(symbol: str, what_to_show: str = DEFAULT_BASIS) -> pd.DataFrame | None:
    """Load all cached daily bars for a symbol (OHLCV, DatetimeIndex).

    Returns None on a cache miss — call prewarm() once with TWS up to populate.
    """
    return _mdb.get_bars(symbol, bar_size=DEFAULT_BAR_SIZE, what_to_show=what_to_show)


def load_panels(symbols: list[str], what_to_show: str = DEFAULT_BASIS,
                field: str = None) -> dict[str, pd.DataFrame]:
    """Load cached daily bars for several symbols. Missing symbols raise.

    Returns symbol -> OHLCV DataFrame. Use align_panels() to build aligned
    close/open matrices on the common trading calendar.
    """
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for sym in symbols:
        df = load_daily(sym, what_to_show=what_to_show)
        if df is None or df.empty:
            missing.append(sym)
        else:
            out[sym] = df
    if missing:
        raise FileNotFoundError(
            f"No cached {what_to_show} daily bars for {missing}. Prewarm first: "
            f"python -m research.killed.basket_statarb.data PREWARM {' '.join(missing)}")
    return out


def align_panels(panels: dict[str, pd.DataFrame], symbols: list[str]
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Inner-join several symbols' bars into aligned (closes, opens) matrices.

    Columns follow `symbols` order (so weight vectors align positionally); rows
    are the dates every symbol has in common, oldest-first.
    """
    closes = pd.DataFrame({s: panels[s]["close"] for s in symbols}).dropna()
    opens = pd.DataFrame({s: panels[s]["open"] for s in symbols}).dropna()
    common = closes.index.intersection(opens.index)
    closes = closes.loc[common, symbols].sort_index()
    opens = opens.loc[common, symbols].sort_index()
    return closes, opens


def load_pair_panels(sym_a: str, sym_b: str, what_to_show: str = DEFAULT_BASIS
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience: aligned (closes, opens) for a two-name basket."""
    panels = load_panels([sym_a, sym_b], what_to_show=what_to_show)
    return align_panels(panels, [sym_a, sym_b])


# ----------------------------------------------------------------------
# Prewarm (requires TWS)
# ----------------------------------------------------------------------

def prewarm(symbols: list[str], duration: str = "15 Y",
            what_to_show: str = DEFAULT_BASIS) -> dict[str, int]:
    """Fetch daily bars for each symbol via a research Session and cache them.

    Requests are serialized with a delay to respect IBKR pacing. The IBKR client
    upserts every fetch into market_data_bars, so subsequent loads are offline.

    Returns symbol -> rows fetched. Requires TWS running on the paper port.
    """
    # Imported lazily so the offline cache path never pulls in the TWS stack.
    from research.session import Session

    fetched: dict[str, int] = {}
    with Session() as session:
        for i, sym in enumerate(symbols):
            try:
                df = session.market_data.get_daily_bars(
                    sym, duration=duration, what_to_show=what_to_show)
                fetched[sym] = 0 if df is None else len(df)
                logger.info(f"Prewarmed {sym}: {fetched[sym]} bars "
                            f"({i + 1}/{len(symbols)})")
            except Exception as e:  # noqa: BLE001 — research convenience; log and continue
                logger.error(f"Prewarm failed for {sym}: {e}")
                fetched[sym] = 0
            if i < len(symbols) - 1:
                time.sleep(_PREWARM_DELAY)
    return fetched


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0].upper() != "PREWARM" or len(args) < 2:
        print("Usage: python -m research.killed.basket_statarb.data PREWARM SYM [SYM ...] "
              "[duration='15 Y']")
        raise SystemExit(1)
    syms = [a for a in args[1:] if " " not in a]
    dur = next((a for a in args[1:] if " " in a), "15 Y")
    counts = prewarm(syms, duration=dur)
    print("Prewarm complete:")
    for s, n in counts.items():
        print(f"  {s:8s} {n} bars")
