"""
eda.py — Step 3 signal EDA for the intraday conditional strategy (QQQ).

PURE EDA. No strategy module, no backtester, no orders. Reads QQQ 5-min TRADES
bars straight from the local `market_data_bars` cache and characterizes the
H1 conditional relationship between the first-hour return and the rest-of-day
return. See research/research_notes/intraday_conditional_strategy_notes.md
(Step 3) for the spec, and this directory's README.md.

SAMPLE DISCIPLINE (enforced at the data layer):
  * IS WINDOW = 2015-01-01 .. 2021-12-31 — the ONLY evaluated sample.
  * 2014 is WARM-UP ONLY: it populates the trailing-ATR and overnight-gap
    features for early-2015 days, but never enters any statistic, plot, or
    bucket (dropped by the IS date mask after features are built).
  * OOS (2022+) is NEVER loaded: the SQL query is bounded `bar_datetime <
    '2022-01-01'`, so post-2021 data never reaches this process. A runtime
    assertion re-checks this after the load.
  * Half-days (1pm ET close) and IBKR-gap days from half_days.json are dropped,
    not imputed.

BAR-EDGE CONVENTION (IBKR 5-min bars are START-labeled; bars stored in US/Central
and converted to US/Eastern here). "close at TIME T" = close of the bar ENDING at
T = close of the (T - 5min) START-labeled bar — consistent with the spec's
"close at 10:30 = close of the 10:25 bar". Concretely:
    open_0930    = open  of the 09:30 ET bar (session open)
    close_1030   = close of the 10:25 ET bar   (== "close at 10:30")
    close_1555   = close of the 15:50 ET bar   (== "close at 15:55"; this also
                   ~= the open of the 15:55 bar, i.e. the strategy's flat-into-
                   close exit fill, so the EDA measure matches the tradeable exit)
    session_close = close of the 15:55 ET bar  (true 16:00 close; used only for
                   the NEXT day's overnight gap)

FEATURES:
    r_1h  = close_1030 / open_0930 - 1            (first 60 min: 09:30 -> 10:25)
    r_30  = close_1000 / open_0930 - 1            (30-min window, robustness)
    r_90  = close_1100 / open_0930 - 1            (90-min window, robustness)
    r_rod = close_1555 / close_1030 - 1           (rest of day: 10:25 -> 15:50)
    overnight_gap = open_0930 / prior_session_close - 1
    ATR_pit = trailing-20-day, POINT-IN-TIME mean of daily (high-low)/open over
              the 20 valid trading days STRICTLY BEFORE day t (shift(1).rolling(20)).
    m       = |r_1h| / ATR_pit                    (vol-normalized magnitude)
    signed_rod = sign(r_1h) * r_rod               (>0 continuation, <0 fade)

BUCKETS (committed once, NOT searched): SEXTILES of m, edges = the 1/6..5/6
quantiles of the IS m-distribution, fixed once. Each window (30/60/90) gets its
own sextile edges from its own m.

Run:  python -m research.killed.intraday_conditional.eda
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: save PNGs, never open a window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# The readout uses non-ASCII (×, σ, →); force UTF-8 so a Windows cp1252 console
# doesn't mojibake it.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DB_PATH = HERE.parent.parent / "data" / "paperstreet.db"
PLOTS_DIR = HERE / "plots"
HALF_DAYS_JSON = HERE / "half_days.json"

SYMBOL = "QQQ"
BAR_SIZE = "5 mins"
WHAT_TO_SHOW = "TRADES"

WARMUP_START = "2014-01-01"   # warm-up floor (only feeds trailing features)
IS_START = pd.Timestamp("2015-01-01")
IS_END = pd.Timestamp("2021-12-31")
OOS_FLOOR = "2022-01-01"       # SQL upper bound (exclusive): OOS never loaded

ATR_LOOKBACK = 20

# Step-4 cost assumptions (BACKTESTING.md / Step 4 of the notes), for the
# cost-floor overlay. IBKR tiered: $0.0035/share, $0.35 min; slippage 0.5 bps
# per side at 10:30 entry and 0.5 bps per side at 15:55 exit.
COMMISSION_PER_SHARE = 0.0035
COMMISSION_MIN = 0.35
SLIPPAGE_BPS_PER_SIDE = 0.5
REF_NOTIONAL = 50_000.0        # reference trade notional for the commission-bps term

# ET clock times of the START-labeled bars we key on.
T_OPEN = "09:30"               # session open bar
T_30 = "09:55"                 # close == "close at 10:00" (30-min window end)
T_60 = "10:25"                 # close == "close at 10:30" (60-min window end)
T_90 = "10:55"                 # close == "close at 11:00" (90-min window end)
T_ROD_END = "15:50"            # close == "close at 15:55" (rest-of-day end)
T_SESSION_CLOSE = "15:55"      # final bar; close == true 16:00 session close


# ---------------------------------------------------------------------------
# Load — cache-first, IS-bounded, OOS never touched
# ---------------------------------------------------------------------------

def load_bars_et() -> pd.DataFrame:
    """Load QQQ 5-min TRADES bars from the cache, [2014-01-01, 2022-01-01),
    indexed by US/Eastern timestamp. OOS (2022+) is excluded in SQL."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found at {DB_PATH}")
    sql = """
        SELECT bar_datetime, open, high, low, close, volume
        FROM market_data_bars
        WHERE symbol = ? AND bar_size = ? AND what_to_show = ?
          AND bar_datetime >= ? AND bar_datetime < ?
        ORDER BY bar_datetime ASC
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            sql, (SYMBOL, BAR_SIZE, WHAT_TO_SHOW, WARMUP_START, OOS_FLOOR)
        ).fetchall()
    if not rows:
        raise RuntimeError("No QQQ 5-min TRADES bars found in cache for the window.")
    df = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
    # Stored tz-aware in US/Central; parse as UTC then convert to ET so the
    # session clock (09:30..15:55) is correct year-round through DST.
    idx = pd.to_datetime(df["dt"], utc=True).dt.tz_convert("US/Eastern")
    df = df.drop(columns="dt").set_index(idx).sort_index()
    df.index.name = "dt_et"
    # Hard guard: OOS must be absent.
    assert df.index.max().tz_localize(None) < pd.Timestamp(OOS_FLOOR), \
        "OOS leak: post-2021 bar present in the loaded frame."
    return df


def _load_excluded_days() -> set[str]:
    """Half-days + IBKR-gap days from half_days.json (ISO date strings)."""
    spec = json.loads(HALF_DAYS_JSON.read_text())
    return set(spec.get("half_days", [])) | set(spec.get("incomplete_days_ibkr_gap", []))


# ---------------------------------------------------------------------------
# Feature construction (daily granularity)
# ---------------------------------------------------------------------------

def build_daily_features(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per valid trading day with all H1 features.

    Valid day = a full session that is not a half-day or IBKR-gap day AND has
    every keyed bar present (missing keys -> NaN -> dropped). Trailing features
    (ATR_pit, gap) use the prior VALID days, so a dropped day is simply skipped
    in the lookback — never imputed.
    """
    df = bars.copy()
    df["date"] = df.index.normalize().tz_localize(None)
    df["time"] = df.index.strftime("%H:%M")

    # Pivot keyed prices to (date x time). Unique (date,time) guaranteed by the
    # bar grid, so pivot is well-defined.
    closes = df.pivot(index="date", columns="time", values="close")
    opens = df.pivot(index="date", columns="time", values="open")
    day_high = df.groupby("date")["high"].max()
    day_low = df.groupby("date")["low"].min()
    bar_count = df.groupby("date")["close"].size()

    feat = pd.DataFrame(index=closes.index)
    feat["n_bars"] = bar_count
    feat["open_0930"] = opens.get(T_OPEN)
    feat["close_1000"] = closes.get(T_30)
    feat["close_1030"] = closes.get(T_60)
    feat["close_1100"] = closes.get(T_90)
    feat["close_1555"] = closes.get(T_ROD_END)
    feat["session_close"] = closes.get(T_SESSION_CLOSE)
    feat["day_high"] = day_high
    feat["day_low"] = day_low

    # Drop explicitly-excluded days (half-days, IBKR gaps) before anything else,
    # so they never enter the trailing windows or the statistics.
    excluded = _load_excluded_days()
    feat = feat[~feat.index.strftime("%Y-%m-%d").isin(excluded)]

    # Require every keyed bar; anything missing is not a clean full session.
    required = ["open_0930", "close_1000", "close_1030", "close_1100",
                "close_1555", "session_close", "day_high", "day_low"]
    feat = feat.dropna(subset=required).copy()

    # Returns
    feat["r_30"] = feat["close_1000"] / feat["open_0930"] - 1.0
    feat["r_1h"] = feat["close_1030"] / feat["open_0930"] - 1.0
    feat["r_90"] = feat["close_1100"] / feat["open_0930"] - 1.0
    feat["r_rod_30"] = feat["close_1555"] / feat["close_1000"] - 1.0
    feat["r_rod_60"] = feat["close_1555"] / feat["close_1030"] - 1.0
    feat["r_rod_90"] = feat["close_1555"] / feat["close_1100"] - 1.0

    # Point-in-time trailing ATR: daily (high-low)/open over the prior 20 valid
    # days, STRICTLY before t (shift(1) excludes the same day; rolling(20) with
    # min_periods=20 leaves the first 20 days NaN — that's what 2014 warm-up is for).
    feat["range_pct"] = (feat["day_high"] - feat["day_low"]) / feat["open_0930"]
    feat["atr_pit"] = feat["range_pct"].shift(1).rolling(ATR_LOOKBACK, min_periods=ATR_LOOKBACK).mean()

    # Overnight gap vs the prior valid session's true close.
    feat["overnight_gap"] = feat["open_0930"] / feat["session_close"].shift(1) - 1.0

    # Normalized magnitudes and signed rest-of-day, per window.
    for win, rcol, rodcol in [(30, "r_30", "r_rod_30"),
                              (60, "r_1h", "r_rod_60"),
                              (90, "r_90", "r_rod_90")]:
        feat[f"m_{win}"] = feat[rcol].abs() / feat["atr_pit"]
        feat[f"signed_rod_{win}"] = np.sign(feat[rcol]) * feat[rodcol]

    feat["year"] = feat.index.year
    return feat


def is_slice(feat: pd.DataFrame) -> pd.DataFrame:
    """IS-only rows (2015-01-01 .. 2021-12-31) with ATR populated. 2014 warm-up
    drops out here; everything downstream is IS-only."""
    mask = (feat.index >= IS_START) & (feat.index <= IS_END) & feat["atr_pit"].notna()
    out = feat[mask].copy()
    assert out.index.max() <= IS_END, "IS slice contains post-2021 rows."
    return out


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _tstat(x: pd.Series) -> float:
    x = x.dropna()
    n = len(x)
    if n < 2:
        return np.nan
    sd = x.std(ddof=1)
    if sd == 0:
        return np.nan
    return x.mean() / (sd / np.sqrt(n))


def sextile_edges(m: pd.Series) -> np.ndarray:
    """The five interior sextile edges (1/6..5/6 quantiles), fixed once on IS."""
    qs = np.quantile(m.dropna(), [1 / 6, 2 / 6, 3 / 6, 4 / 6, 5 / 6])
    return qs


def assign_sextile(m: pd.Series, edges: np.ndarray) -> pd.Series:
    """Sextile label 1..6 from fixed interior edges (closed at the ends)."""
    bins = np.concatenate([[-np.inf], edges, [np.inf]])
    return pd.cut(m, bins=bins, labels=[1, 2, 3, 4, 5, 6], include_lowest=True)


def crosstab(feat: pd.DataFrame, win: int, edges: np.ndarray | None = None):
    """Per-sextile mean/median signed_rod (bps), t-stat, n for one window.
    Returns (table_df, edges)."""
    mcol, scol = f"m_{win}", f"signed_rod_{win}"
    sub = feat[[mcol, scol]].dropna()
    if edges is None:
        edges = sextile_edges(sub[mcol])
    sub = sub.assign(bucket=assign_sextile(sub[mcol], edges))
    g = sub.groupby("bucket", observed=True)[scol]
    table = pd.DataFrame({
        "n": g.size(),
        "mean_bps": g.mean() * 1e4,
        "median_bps": g.median() * 1e4,
        "se_bps": g.std(ddof=1) / np.sqrt(g.size()) * 1e4,
        "t_stat": g.apply(_tstat),
        "m_lo": sub.groupby("bucket", observed=True)[mcol].min(),
        "m_hi": sub.groupby("bucket", observed=True)[mcol].max(),
    })
    return table, edges


def cost_floor_bps(ref_price: float) -> dict:
    """Round-trip cost floor in bps from Step-4 assumptions, at REF_NOTIONAL."""
    slippage_rt = 2 * SLIPPAGE_BPS_PER_SIDE
    shares = REF_NOTIONAL / ref_price
    comm_side = max(COMMISSION_MIN, COMMISSION_PER_SHARE * shares)
    comm_rt_bps = 2 * comm_side / REF_NOTIONAL * 1e4
    return {
        "slippage_rt_bps": slippage_rt,
        "commission_rt_bps": comm_rt_bps,
        "total_rt_bps": slippage_rt + comm_rt_bps,
        "ref_price": ref_price,
        "shares": shares,
    }


# ---------------------------------------------------------------------------
# EDA Output 1 — intraday vol profile
# ---------------------------------------------------------------------------

def vol_profile(bars: pd.DataFrame, excluded: set[str]) -> pd.DataFrame:
    """Realized vol (std of 5-min bar return) per intraday bar slot, by year, IS only."""
    df = bars.copy()
    d = df.index.normalize().tz_localize(None)
    in_is = (d >= IS_START) & (d <= IS_END)
    not_excl = ~df.index.strftime("%Y-%m-%d").isin(excluded)
    df = df[in_is & not_excl].copy()
    df["bar_ret"] = df["close"] / df["open"] - 1.0
    df["time"] = df.index.strftime("%H:%M")
    df["year"] = df.index.year
    prof = df.groupby(["year", "time"])["bar_ret"].std().unstack(0) * 1e4  # bps
    return prof


def plot_vol_profile(prof: pd.DataFrame, path: Path) -> dict:
    fig, ax = plt.subplots(figsize=(11, 5))
    for year in prof.columns:
        ax.plot(prof.index, prof[year], label=str(year), alpha=0.8)
    ax.set_title("QQQ intraday realized vol profile — std of 5-min bar return by slot (IS, by year)")
    ax.set_xlabel("ET bar start"); ax.set_ylabel("realized vol (bps)")
    ax.set_xticks(prof.index[::6]); ax.tick_params(axis="x", rotation=90)
    ax.legend(ncol=4, fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
    # Quantify open/10:30 vs midday.
    mean_prof = prof.mean(axis=1)
    open_v = mean_prof.get("09:30", np.nan)
    at_1030 = mean_prof.get(T_60, np.nan)   # the 10:25 bar (end of first hour)
    midday = mean_prof.get("12:00", np.nan)
    return {"open_bps": open_v, "bar_1025_bps": at_1030, "midday_1200_bps": midday,
            "ratio_1025_midday": at_1030 / midday if midday else np.nan,
            "ratio_open_midday": open_v / midday if midday else np.nan}


# ---------------------------------------------------------------------------
# EDA Output 2 — first-hour return distribution
# ---------------------------------------------------------------------------

def plot_r1h_distribution(feat: pd.DataFrame, path: Path) -> dict:
    r1h = feat["r_1h"].dropna()
    gap = feat["overnight_gap"].dropna()
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    axes[0].hist(r1h * 1e4, bins=80, alpha=0.7, color="steelblue", label="r_1h")
    axes[0].hist(gap * 1e4, bins=80, alpha=0.45, color="darkorange", label="overnight_gap")
    axes[0].set_title("First-hour return vs overnight gap (IS)")
    axes[0].set_xlabel("return (bps)"); axes[0].set_ylabel("days"); axes[0].legend()
    axes[0].grid(alpha=0.3)
    # Time series by year (annual std as a proxy for regime).
    by_year_r1h = r1h.groupby(feat.loc[r1h.index, "year"]).std() * 1e4
    by_year_gap = gap.groupby(feat.loc[gap.index, "year"]).std() * 1e4
    x = by_year_r1h.index
    w = 0.4
    axes[1].bar(x - w / 2, by_year_r1h.values, width=w, label="r_1h std", color="steelblue")
    axes[1].bar(x + w / 2, by_year_gap.values, width=w, label="gap std", color="darkorange")
    axes[1].set_title("Daily std by year (bps)")
    axes[1].set_xlabel("year"); axes[1].set_ylabel("std (bps)"); axes[1].legend()
    axes[1].grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
    ann = lambda s: s.std() * np.sqrt(252)
    return {
        "r1h_mean_bps": r1h.mean() * 1e4, "r1h_std_bps": r1h.std() * 1e4,
        "r1h_ann_vol": ann(r1h), "gap_std_bps": gap.std() * 1e4, "gap_ann_vol": ann(gap),
    }


# ---------------------------------------------------------------------------
# EDA Output 3 — the conditional cross-tab (+ cost-floor overlay)
# ---------------------------------------------------------------------------

def plot_crosstab(table: pd.DataFrame, cost: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5.5))
    buckets = table.index.astype(int)
    means = table["mean_bps"].values
    colors = ["crimson" if v < 0 else "seagreen" for v in means]
    ax.bar(buckets, means, color=colors, alpha=0.85)
    ax.plot(buckets, table["median_bps"].values, "ko--", label="median", alpha=0.7)
    floor = cost["total_rt_bps"]
    ax.axhspan(-floor, floor, color="gray", alpha=0.2,
               label=f"±cost floor ({floor:.2f} bps RT)")
    ax.axhline(0, color="black", lw=0.8)
    for b, m, t, n in zip(buckets, means, table["t_stat"], table["n"]):
        ax.annotate(f"t={t:.1f}\nn={n}", (b, m), ha="center",
                    va="bottom" if m >= 0 else "top", fontsize=8)
    ax.set_title("Signed rest-of-day return by |r_1h|/ATR sextile (60-min, IS)\n"
                 "low sextile = small move (fade?), high = large move (continue?)")
    ax.set_xlabel("m sextile (1=smallest |r_1h|/ATR → 6=largest)")
    ax.set_ylabel("mean signed r_rod (bps)")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ---------------------------------------------------------------------------
# EDA Output 4 — overnight-gap disentangling
# ---------------------------------------------------------------------------

def gap_conditioned_crosstab(feat: pd.DataFrame, edges: np.ndarray) -> pd.DataFrame:
    """60-min cross-tab split by gap sign AND a small/large gap split (median |gap|)."""
    sub = feat[["m_60", "signed_rod_60", "overnight_gap"]].dropna().copy()
    sub["bucket"] = assign_sextile(sub["m_60"], edges)
    med_abs_gap = sub["overnight_gap"].abs().median()
    sub["gap_grp"] = np.where(
        sub["overnight_gap"] >= 0,
        np.where(sub["overnight_gap"].abs() >= med_abs_gap, "up_large", "up_small"),
        np.where(sub["overnight_gap"].abs() >= med_abs_gap, "dn_large", "dn_small"),
    )
    rows = []
    for grp, gdf in sub.groupby("gap_grp"):
        g = gdf.groupby("bucket", observed=True)["signed_rod_60"]
        for bucket, vals in g:
            rows.append({"gap_grp": grp, "bucket": int(bucket), "n": len(vals),
                         "mean_bps": vals.mean() * 1e4, "t_stat": _tstat(vals)})
    return pd.DataFrame(rows), med_abs_gap


def plot_gap_conditioning(gap_tab: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for grp, gdf in gap_tab.groupby("gap_grp"):
        gdf = gdf.sort_values("bucket")
        ax.plot(gdf["bucket"], gdf["mean_bps"], marker="o", label=grp, alpha=0.85)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Signed r_rod by m-sextile, conditioned on overnight gap (60-min, IS)")
    ax.set_xlabel("m sextile"); ax.set_ylabel("mean signed r_rod (bps)")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ---------------------------------------------------------------------------
# EDA Output 5 — regime dependence (year by year)
# ---------------------------------------------------------------------------

def yearly_crosstab(feat: pd.DataFrame, edges: np.ndarray) -> pd.DataFrame:
    """Mean signed_rod (bps) per (year, sextile) using the FIXED full-IS edges."""
    sub = feat[["m_60", "signed_rod_60", "year"]].dropna().copy()
    sub["bucket"] = assign_sextile(sub["m_60"], edges)
    tab = sub.groupby(["year", "bucket"], observed=True)["signed_rod_60"].mean().unstack("bucket") * 1e4
    return tab


def plot_regime(tab: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(tab.values, aspect="auto", cmap="RdYlGn",
                   vmin=-np.nanmax(np.abs(tab.values)), vmax=np.nanmax(np.abs(tab.values)))
    ax.set_xticks(range(len(tab.columns))); ax.set_xticklabels(tab.columns)
    ax.set_yticks(range(len(tab.index))); ax.set_yticklabels(tab.index)
    ax.set_xlabel("m sextile"); ax.set_ylabel("year")
    ax.set_title("Mean signed r_rod (bps) by year × m-sextile (60-min, IS)\n"
                 "read the SIGN PATTERN stability across rows, not per-cell significance")
    for i in range(tab.shape[0]):
        for j in range(tab.shape[1]):
            v = tab.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, label="mean signed r_rod (bps)")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ---------------------------------------------------------------------------
# EDA Output 6 — window-length robustness
# ---------------------------------------------------------------------------

def plot_window_robustness(feat: pd.DataFrame, path: Path) -> dict:
    fig, ax = plt.subplots(figsize=(11, 5.5))
    tables = {}
    for win, style in [(30, "o-"), (60, "s-"), (90, "^-")]:
        tab, _ = crosstab(feat, win)
        tables[win] = tab
        ax.plot(tab.index.astype(int), tab["mean_bps"], style, label=f"{win}-min", alpha=0.85)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Window-length robustness: signed r_rod by m-sextile, 30/60/90-min (IS)")
    ax.set_xlabel("m sextile"); ax.set_ylabel("mean signed r_rod (bps)")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
    return tables


# ---------------------------------------------------------------------------
# Readout
# ---------------------------------------------------------------------------

def _fmt_table(table: pd.DataFrame) -> str:
    lines = [f"  {'sext':>4} {'n':>5} {'m_range':>15} {'mean_bps':>9} {'median_bps':>10} {'se_bps':>7} {'t':>6}"]
    for b, row in table.iterrows():
        lines.append(
            f"  {int(b):>4} {int(row['n']):>5} "
            f"[{row['m_lo']:.2f},{row['m_hi']:.2f}]".rjust(16)
            + f" {row['mean_bps']:>9.2f} {row['median_bps']:>10.2f} {row['se_bps']:>7.2f} {row['t_stat']:>6.2f}"
        )
    return "\n".join(lines)


def main():
    PLOTS_DIR.mkdir(exist_ok=True)
    excluded = _load_excluded_days()

    print("=" * 78)
    print("STEP 3 EDA — Intraday Conditional Strategy (QQQ)  |  IS 2015-2021, OOS untouched")
    print("=" * 78)

    bars = load_bars_et()
    feat_all = build_daily_features(bars)
    feat = is_slice(feat_all)

    print(f"\nLoaded bars: {len(bars):,}  ({bars.index.min().date()} .. {bars.index.max().date()})")
    print(f"Valid IS trading days (full sessions, half/gap days dropped, ATR populated): {len(feat)}")
    print(f"  IS span: {feat.index.min().date()} .. {feat.index.max().date()}")
    print(f"  excluded days from half_days.json: {len(excluded)}")

    ref_price = feat["close_1030"].mean()
    cost = cost_floor_bps(ref_price)
    print(f"\nCost floor (Step-4 assumptions, ref notional ${REF_NOTIONAL:,.0f}, "
          f"ref price ${ref_price:.0f}):")
    print(f"  slippage {cost['slippage_rt_bps']:.2f} bps RT + commission "
          f"{cost['commission_rt_bps']:.2f} bps RT = {cost['total_rt_bps']:.2f} bps round-trip")
    print(f"  (Step 6 stresses 2x -> ~{2*cost['total_rt_bps']:.2f} bps; task band 1.5-2 bps)")

    # 1. Vol profile
    prof = vol_profile(bars, excluded)
    vp = plot_vol_profile(prof, PLOTS_DIR / "01_vol_profile.png")
    print("\n[1] INTRADAY VOL PROFILE (mean across IS years, bps per 5-min bar):")
    print(f"  09:30 open bar: {vp['open_bps']:.1f}  |  10:25 (end-1h) bar: {vp['bar_1025_bps']:.1f}"
          f"  |  12:00 midday: {vp['midday_1200_bps']:.1f}")
    print(f"  ratio 10:25/midday = {vp['ratio_1025_midday']:.2f}x ; open/midday = {vp['ratio_open_midday']:.2f}x")

    # 2. r_1h distribution
    rd = plot_r1h_distribution(feat, PLOTS_DIR / "02_r1h_distribution.png")
    print("\n[2] FIRST-HOUR RETURN DISTRIBUTION (IS):")
    print(f"  r_1h: mean {rd['r1h_mean_bps']:.2f} bps, std {rd['r1h_std_bps']:.1f} bps, "
          f"annualized vol {rd['r1h_ann_vol']:.1%}")
    print(f"  overnight gap: std {rd['gap_std_bps']:.1f} bps, annualized vol {rd['gap_ann_vol']:.1%}")

    # 3. Conditional cross-tab (the key test)
    table60, edges60 = crosstab(feat, 60)
    plot_crosstab(table60, cost, PLOTS_DIR / "03_conditional_crosstab.png")
    print("\n[3] CONDITIONAL CROSS-TAB — signed r_rod by m-sextile (60-min) — KEY TEST of H1:")
    print(f"  sextile edges (m=|r_1h|/ATR), fixed once on IS: "
          f"{', '.join(f'{e:.3f}' for e in edges60)}")
    print(_fmt_table(table60))
    floor = cost["total_rt_bps"]
    # A bucket only offers a *tradeable* edge if its gross mean both (a) clears
    # the cost floor and (b) is statistically distinguishable from zero (|t|>2,
    # before any multiple-testing haircut across 6 buckets). |mean|>floor alone
    # is meaningless when the per-bucket SE (~4-5 bps) dwarfs the floor.
    nominal = table60[table60["mean_bps"].abs() > floor]
    print(f"  buckets with |mean| > cost floor ({floor:.2f} bps), NOMINAL only: "
          f"{list(nominal.index.astype(int)) if len(nominal) else 'NONE'}")
    significant = table60[table60["t_stat"].abs() > 2.0]
    print(f"  buckets with |t| > 2 (distinguishable from zero at all): "
          f"{list(significant.index.astype(int)) if len(significant) else 'NONE'}")
    real = table60[(table60["mean_bps"].abs() - floor > 2 * table60["se_bps"])
                   & (table60["t_stat"].abs() > 2.0)]
    print(f"  buckets whose NET edge clears the floor by >2 SE AND |t|>2 "
          f"(actually tradeable): {list(real.index.astype(int)) if len(real) else 'NONE'}")
    # rough annualized-edge sanity (nominal, for scale only — NOT a claim of edge)
    days_per_yr = len(feat) / feat["year"].nunique()
    print("  rough annualized scale (nominal, ignores that none are significant):")
    for b, row in nominal.iterrows():
        net = abs(row["mean_bps"]) - floor
        trades_yr = days_per_yr / 6.0  # one sextile ~ 1/6 of days
        print(f"    sextile {int(b)}: net {net:.2f} bps/trade (±{row['se_bps']:.1f} SE) "
              f"× ~{trades_yr:.0f} trades/yr = ~{net*trades_yr:.0f} bps/yr "
              f"({'continue' if row['mean_bps']>0 else 'fade'})")

    # 4. Gap conditioning
    gap_tab, med_abs_gap = gap_conditioned_crosstab(feat, edges60)
    plot_gap_conditioning(gap_tab, PLOTS_DIR / "04_gap_conditioning.png")
    print("\n[4] OVERNIGHT-GAP DISENTANGLING (60-min, IS):")
    print(f"  median |overnight gap| = {med_abs_gap*1e4:.1f} bps (small/large split)")
    piv = gap_tab.pivot(index="bucket", columns="gap_grp", values="mean_bps")
    print(piv.round(1).to_string())

    # 5. Regime dependence
    ytab = yearly_crosstab(feat, edges60)
    plot_regime(ytab, PLOTS_DIR / "05_regime_by_year.png")
    print("\n[5] REGIME DEPENDENCE — mean signed r_rod (bps) by year × sextile (60-min):")
    print(ytab.round(1).to_string())
    # Sign-pattern read: do the tails (sext 1 fade-ish, sext 6 continue-ish) hold sign?
    sign_lo = np.sign(ytab[1]); sign_hi = np.sign(ytab[6]) if 6 in ytab.columns else None

    # 6. Window robustness
    wtables = plot_window_robustness(feat, PLOTS_DIR / "06_window_robustness.png")
    print("\n[6] WINDOW-LENGTH ROBUSTNESS — mean signed r_rod (bps) by sextile:")
    hdr = "  sext " + " ".join(f"{w}min".rjust(9) for w in (30, 60, 90))
    print(hdr)
    for b in range(1, 7):
        vals = []
        for w in (30, 60, 90):
            v = wtables[w]["mean_bps"].get(b, np.nan)
            vals.append(f"{v:>9.2f}")
        print(f"  {b:>4} " + " ".join(vals))

    print("\n" + "=" * 78)
    print("Plots saved to:", PLOTS_DIR)
    print("=" * 78)

    return {
        "feat": feat, "table60": table60, "edges60": edges60, "cost": cost,
        "vol_profile": prof, "gap_tab": gap_tab, "ytab": ytab, "wtables": wtables,
    }


if __name__ == "__main__":
    main()
