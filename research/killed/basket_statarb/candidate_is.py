"""
candidate_is.py — Stage-0 candidate IN-SAMPLE pipeline (Steps 4–6).

Runs the four pre-committed clusters (A/B/C/D, Open Decision #3) through the
in-sample half of the single IS/OOS split, per method (Box-Tiao primary, Johansen
cross-check) and per basis (TRADES + ADJUSTED_LAST), and:

  - estimates ALL-constituent eigenvector weights on the IS window (no subset
    search, no NES — Decisions #1/#4),
  - builds the raw share-weighted spread, rolling z-score, ADF, OU half-life,
  - measures weight stability across two adjacent IS sub-windows,
  - runs the research-tier sim on the IS window with the frozen policy and full
    cost model (commission + slippage + 50bp borrow + signed per-leg dividend
    overlay; overlay ON under TRADES, OFF under ADJUSTED_LAST),
  - applies the IS STRUCTURAL gates (Decision #4),
  - for gate-clearers: a parameter-sensitivity sweep (plateau check) and a cost
    stress (borrow 50→200bp, slippage up),
  - emits one candidate spec (JSON) per cluster/basis.

HARD BOUNDARY: this module reads ONLY in-sample data (bar_datetime <= IS_END).
The OOS one-shot (Step 7) is a separate later task and MUST NOT run here. The
boundary is asserted at load time.

This is research/ tooling, NOT the validation gate (Decision #8): the PaperStreet
backtesting/ engine is the gate at Stage 1.

Run (offline, after the candidate names are prewarmed on both bases):
    python -m research.killed.basket_statarb.candidate_is              # full readout + specs
    python -m research.killed.basket_statarb.candidate_is --no-specs   # readout only
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from research.killed.basket_statarb import cointegration, data, spread
from research.killed.basket_statarb.costs import CostModel
from research.killed.basket_statarb.sim import simulate, SimResult

# ----------------------------------------------------------------------
# FROZEN CONFIG (pre-committed; provenance for every emitted spec)
# ----------------------------------------------------------------------

# IS/OOS split (Open Decision #2): ~5y IS from ~2017 incl. the 2020 COVID break,
# OOS = 2023→present. Locked from data availability (SNAP starts 2017-03, so
# Cluster A's IS is ragged at the front — flagged in the readout). This task
# reads ONLY [IS_START, IS_END].
IS_START = "2017-01-01"
IS_END = "2022-12-31"
OOS_START = "2023-01-01"   # not read here; recorded for provenance only

# Clusters (Open Decision #3) — SNAP kept in A (Kyle 2026-06-17; borrow verified
# separately in TWS). Report ALL FOUR (multiplicity discipline).
CLUSTERS = {
    "A_ad_internet": ["META", "GOOGL", "GOOG", "SNAP"],
    "B_money_center_banks": ["JPM", "BAC", "C", "WFC"],
    "C_cloud_software": ["MSFT", "AMZN", "CRM", "ORCL"],
    "D_large_semis": ["NVDA", "AMD", "INTC", "AVGO", "QCOM"],
}

BASES = ["TRADES", "ADJUSTED_LAST"]
METHODS = ["box_tiao", "johansen"]   # box_tiao primary, johansen cross-check

# Frozen trading policy (Stage-0 defaults).
POLICY = dict(z_enter=2.0, z_exit=0.5, z_stop=3.5, time_stop_mult=3.0,
              lookback=60, gross_notional=10_000.0)

# Cost model (commission + slippage + borrow). Dividend overlay handled per-basis
# via DIVIDEND_YIELDS below (ON for TRADES, OFF for ADJUSTED_LAST).
COSTS = CostModel(commission_per_share=0.005, commission_min=1.0,
                  half_spread_bps=1.0, borrow_bps_annual=50.0,
                  dividend_yield_annual_bps=0.0)

# Approximate FLAT annual dividend yields (fractions), hand-entered Stage-0
# estimates (Open Decision #5 — not an ex-date schedule). Used ONLY on the TRADES
# basis (overlay ON); ignored on ADJUSTED_LAST (overlay OFF, dividends already in
# the total-return path). Cluster-B banks are the names where this actually bites.
DIVIDEND_YIELDS = {
    "META": 0.000, "GOOGL": 0.000, "GOOG": 0.000, "SNAP": 0.000,
    "JPM": 0.026, "BAC": 0.022, "C": 0.035, "WFC": 0.025,
    "MSFT": 0.009, "AMZN": 0.000, "CRM": 0.000, "ORCL": 0.015,
    "NVDA": 0.001, "AMD": 0.000, "INTC": 0.025, "AVGO": 0.030, "QCOM": 0.025,
}

# IS structural gates (Open Decision #4 — hard; any miss is an automatic park).
GATE_ADF_MAX = 0.05
GATE_HL_MIN, GATE_HL_MAX = 2.0, 15.0
GATE_WSTAB_MIN = 0.85
GATE_IS_ROUNDTRIPS_MIN = 25

SPEC_DIR = Path(__file__).resolve().parent / "specs"


# ----------------------------------------------------------------------
# IS data loading (boundary-enforced)
# ----------------------------------------------------------------------

def load_is_panels(symbols: list[str], basis: str):
    """Aligned (closes, opens) for `symbols` on `basis`, clipped to the IS window.

    Hard boundary: rows are filtered to [IS_START, IS_END] and the max date is
    asserted <= IS_END so OOS data can never leak into a Stage-0 IS evaluation.
    """
    panels = data.load_panels(symbols, what_to_show=basis)
    closes, opens = data.align_panels(panels, symbols)
    mask = (closes.index >= pd.Timestamp(IS_START)) & (closes.index <= pd.Timestamp(IS_END))
    closes, opens = closes.loc[mask], opens.loc[mask]
    if len(closes):
        assert closes.index.max() <= pd.Timestamp(IS_END), "IS boundary breached"
    return closes, opens


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------

def trade_level_sharpe(sim: SimResult, is_years: float) -> float:
    """Annualized trade-level Sharpe = mean(net)/std(net) * sqrt(trades/yr).

    A research-tier proxy (the sim emits round-trip PnLs, not a daily equity
    curve). Scale-invariant in dollars. NaN when <2 trades or zero dispersion.
    The §4 Sharpe ZONES are an OOS gate, not applied here; this is provenance.
    """
    nets = np.array([t.net_pnl for t in sim.trades], dtype=float)
    if nets.size < 2 or nets.std(ddof=1) == 0 or is_years <= 0:
        return float("nan")
    trades_per_year = nets.size / is_years
    return float(nets.mean() / nets.std(ddof=1) * np.sqrt(trades_per_year))


def max_drawdown(sim: SimResult, gross_notional: float):
    """Max drawdown of the trade-sequence cumulative-net-PnL curve.

    Returns (dd_dollars, dd_fraction_of_gross). Trade-level, not intratrade MTM.
    """
    if not sim.trades:
        return 0.0, 0.0
    equity = np.cumsum([t.net_pnl for t in sim.trades])
    running_max = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    dd = running_max - equity
    dd_dollars = float(dd.max())
    return dd_dollars, dd_dollars / gross_notional


# ----------------------------------------------------------------------
# One cluster × method × basis evaluation
# ----------------------------------------------------------------------

@dataclass
class Readout:
    cluster: str
    basis: str
    method: str
    symbols: list[str]
    n_obs: int
    is_start: str
    is_end: str
    weights: list[float]
    signed_shares: list[float]
    adf_p: float
    half_life: float
    wstab: float
    round_trips: int
    net_pnl: float
    gross_pnl: float
    costs: float
    win_rate: float
    sharpe_is: float
    maxdd_dollars: float
    maxdd_frac: float
    # structural-gate outcomes
    gate_adf: bool
    gate_hl: bool
    gate_wstab: bool
    gate_roundtrips: bool
    gate_pass: bool
    binding: list[str]


def _div_yields_for(symbols: list[str], basis: str):
    """Per-leg yields aligned to `symbols`, or None on ADJUSTED_LAST (overlay OFF)."""
    if basis != "TRADES":
        return None
    return np.array([DIVIDEND_YIELDS.get(s, 0.0) for s in symbols], dtype=float)


def _failed_readout(cluster, basis, method, symbols, closes, reason) -> Readout:
    """A degenerate readout when weight estimation fails (e.g. singular matrix)."""
    n = len(closes)
    nan = float("nan")
    return Readout(
        cluster=cluster, basis=basis, method=method, symbols=symbols, n_obs=n,
        is_start=str(closes.index.min().date()) if n else "",
        is_end=str(closes.index.max().date()) if n else "",
        weights=[], signed_shares=[], adf_p=nan, half_life=nan, wstab=nan,
        round_trips=0, net_pnl=nan, gross_pnl=nan, costs=nan, win_rate=nan,
        sharpe_is=nan, maxdd_dollars=nan, maxdd_frac=nan,
        gate_adf=False, gate_hl=False, gate_wstab=False, gate_roundtrips=False,
        gate_pass=False, binding=[reason])


def evaluate(cluster: str, basis: str, method: str) -> Readout:
    symbols = CLUSTERS[cluster]
    closes, opens = load_is_panels(symbols, basis)
    n = len(closes)
    is_years = ((closes.index.max() - closes.index.min()).days / 365.25) if n else 0.0

    try:
        weights = cointegration.estimate_weights(closes, method=method, price_basis="raw")
    except Exception as e:  # singular matrix (collinear share classes), etc.
        return _failed_readout(cluster, basis, method, symbols, closes,
                               f"weights_failed({type(e).__name__})")
    spr = spread.build_spread(closes, weights)
    diag = spread.diagnose(spr, lookback=POLICY["lookback"])

    # Weight stability across two ADJACENT IS sub-windows (Decision #3/#4).
    half = n // 2
    try:
        w1 = cointegration.estimate_weights(closes.iloc[:half], method=method, price_basis="raw")
        w2 = cointegration.estimate_weights(closes.iloc[half:], method=method, price_basis="raw")
        wstab = spread.weight_stability(w1, w2)
    except Exception:
        wstab = float("nan")

    div = _div_yields_for(symbols, basis)
    sim = simulate(closes, opens, weights, cost_model=COSTS, dividend_yields=div, **POLICY)

    signed_shares = (sim.share_scale * weights).tolist() if np.isfinite(sim.share_scale) else []
    sharpe = trade_level_sharpe(sim, is_years)
    dd_d, dd_f = max_drawdown(sim, POLICY["gross_notional"])

    g_adf = diag.adf_pvalue <= GATE_ADF_MAX
    g_hl = GATE_HL_MIN <= diag.half_life <= GATE_HL_MAX
    g_ws = (not np.isnan(wstab)) and wstab >= GATE_WSTAB_MIN
    g_rt = sim.n_trades >= GATE_IS_ROUNDTRIPS_MIN
    binding = [name for name, ok in
               [("ADF", g_adf), ("half_life", g_hl), ("wstab", g_ws), ("round_trips", g_rt)]
               if not ok]

    return Readout(
        cluster=cluster, basis=basis, method=method, symbols=symbols, n_obs=n,
        is_start=str(closes.index.min().date()) if n else "",
        is_end=str(closes.index.max().date()) if n else "",
        weights=weights.tolist(), signed_shares=signed_shares,
        adf_p=diag.adf_pvalue, half_life=diag.half_life, wstab=wstab,
        round_trips=sim.n_trades, net_pnl=sim.net_pnl, gross_pnl=sim.gross_pnl,
        costs=sim.total_costs, win_rate=sim.win_rate, sharpe_is=sharpe,
        maxdd_dollars=dd_d, maxdd_frac=dd_f,
        gate_adf=g_adf, gate_hl=g_hl, gate_wstab=g_ws, gate_roundtrips=g_rt,
        gate_pass=(g_adf and g_hl and g_ws and g_rt), binding=binding,
    )


# ----------------------------------------------------------------------
# Sensitivity (Step 5) and cost stress (Step 6) — gate-clearers only
# ----------------------------------------------------------------------

SENSITIVITY_GRID = dict(
    z_enter=[1.5, 2.0, 2.5],
    z_exit=[0.25, 0.5, 0.75],
    z_stop=[3.0, 3.5, 4.0],
    time_stop_mult=[2.0, 3.0, 4.0],
    lookback=[40, 60, 80],
)


def sensitivity(cluster: str, basis: str, method: str) -> list[dict]:
    """Vary one policy param at a time around the frozen defaults; report a plateau.

    Note: the spread series depends only on weights (fixed) — lookback changes the
    z-score, and the sizing scalar scales shares, but NEITHER changes the spread,
    so ADF/half-life are invariant across this grid by construction (no recompute
    needed; flagged in the readout).
    """
    symbols = CLUSTERS[cluster]
    closes, opens = load_is_panels(symbols, basis)
    weights = cointegration.estimate_weights(closes, method=method, price_basis="raw")
    div = _div_yields_for(symbols, basis)

    rows = []
    for param, values in SENSITIVITY_GRID.items():
        for v in values:
            kw = dict(POLICY)
            kw[param] = v
            sim = simulate(closes, opens, weights, cost_model=COSTS, dividend_yields=div, **kw)
            is_years = (closes.index.max() - closes.index.min()).days / 365.25
            rows.append(dict(param=param, value=v, round_trips=sim.n_trades,
                             net_pnl=sim.net_pnl, sharpe=trade_level_sharpe(sim, is_years)))
    return rows


def cost_stress(cluster: str, basis: str, method: str) -> list[dict]:
    """Stress borrow (50→100→200bp) and slippage (1→2→5bp); confirm edge survives."""
    symbols = CLUSTERS[cluster]
    closes, opens = load_is_panels(symbols, basis)
    weights = cointegration.estimate_weights(closes, method=method, price_basis="raw")
    div = _div_yields_for(symbols, basis)

    rows = []
    for borrow in (50.0, 100.0, 200.0):
        for slip in (1.0, 2.0, 5.0):
            cm = CostModel(commission_per_share=0.005, commission_min=1.0,
                           half_spread_bps=slip, borrow_bps_annual=borrow,
                           dividend_yield_annual_bps=0.0)
            sim = simulate(closes, opens, weights, cost_model=cm, dividend_yields=div, **POLICY)
            rows.append(dict(borrow_bps=borrow, slip_bps=slip,
                             round_trips=sim.n_trades, net_pnl=sim.net_pnl))
    return rows


# ----------------------------------------------------------------------
# Spec emission (Stage-0 output shape)
# ----------------------------------------------------------------------

def emit_spec(r: Readout) -> dict:
    """Build the candidate basket spec (status='candidate', OOS fields empty)."""
    return {
        "identity": {"basket_id": f"{r.cluster}__{r.method}__{r.basis}",
                     "cluster": r.cluster},
        "constituents": [
            {"symbol": s, "weight": w, "signed_shares": (r.signed_shares[i]
                                                         if r.signed_shares else None)}
            for i, (s, w) in enumerate(zip(r.symbols, r.weights))
        ],
        "policy": {**{k: POLICY[k] for k in
                      ("z_enter", "z_exit", "z_stop", "time_stop_mult", "lookback")},
                   "sizing_scalar_gross_notional": POLICY["gross_notional"]},
        "provenance": {
            "method": r.method, "basis": r.basis,
            "is_window": [r.is_start, r.is_end], "is_oos_boundary": OOS_START,
            "price_basis": "raw", "dividend_overlay": (r.basis == "TRADES"),
            "is_metrics": {"adf_p": r.adf_p, "half_life": r.half_life,
                           "wstab": r.wstab, "round_trips": r.round_trips,
                           "sharpe_is": r.sharpe_is, "maxdd_frac": r.maxdd_frac,
                           "net_pnl": r.net_pnl},
            "structural_gate_pass": r.gate_pass, "binding_gates": r.binding,
            "basis_lock": "PROVISIONAL — pending Kyle's §0 basis-lock decision",
        },
        "status": "candidate",
        "oos": {"sharpe": None, "max_drawdown": None, "round_trips": None,
                "half_life": None},
    }


def write_specs(readouts: list[Readout]) -> list[Path]:
    """Write the PRIMARY (box_tiao) spec per cluster/basis to specs/*.json."""
    SPEC_DIR.mkdir(exist_ok=True)
    written = []
    for r in readouts:
        if r.method != "box_tiao":
            continue
        path = SPEC_DIR / f"{r.cluster}__{r.basis}.json"
        path.write_text(json.dumps(emit_spec(r), indent=2))
        written.append(path)
    return written


# ----------------------------------------------------------------------
# Readout printing
# ----------------------------------------------------------------------

def _fmt(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "nan"
    if isinstance(x, float) and np.isinf(x):
        return "inf"
    return f"{x:.{nd}f}"


def print_main_table(rows: list[Readout]):
    hdr = (f"{'cluster':<22}{'basis':<14}{'method':<9}{'n':>5}{'ADFp':>8}"
           f"{'HL':>7}{'wstab':>7}{'RT':>5}{'net$':>9}{'Sharpe':>8}{'maxDD%':>8}{'GATE':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        gate = "PASS" if r.gate_pass else "fail"
        print(f"{r.cluster:<22}{r.basis:<14}{r.method:<9}{r.n_obs:>5}"
              f"{_fmt(r.adf_p, 4):>8}{_fmt(r.half_life, 1):>7}{_fmt(r.wstab, 3):>7}"
              f"{r.round_trips:>5}{_fmt(r.net_pnl, 0):>9}{_fmt(r.sharpe_is, 2):>8}"
              f"{_fmt(100 * r.maxdd_frac, 1):>8}{gate:>6}")
        if not r.gate_pass:
            print(f"{'':<22}  binding: {', '.join(r.binding)}")


def run(write_spec_files: bool = True):
    print(f"\n=== STAGE-0 CANDIDATE IN-SAMPLE READOUT "
          f"(IS {IS_START} to {IS_END}; OOS {OOS_START}+ NOT touched) ===")
    print(f"Gates: ADF<={GATE_ADF_MAX}, half-life in [{GATE_HL_MIN},{GATE_HL_MAX}]d, "
          f"wstab>={GATE_WSTAB_MIN}, IS round-trips>={GATE_IS_ROUNDTRIPS_MIN}. "
          f"Box-Tiao primary, Johansen cross-check.\n")

    all_rows: list[Readout] = []
    for cluster in CLUSTERS:
        for basis in BASES:
            for method in METHODS:
                all_rows.append(evaluate(cluster, basis, method))
    print_main_table(all_rows)

    # Sensitivity + cost stress for any clusters with a gate-clearing run.
    clearers = sorted({r.cluster for r in all_rows if r.gate_pass})
    print(f"\nGate-clearing clusters: {clearers if clearers else 'NONE'}")
    for cluster in clearers:
        # use the primary method + the basis that cleared (prefer box_tiao)
        cleared = [r for r in all_rows if r.cluster == cluster and r.gate_pass]
        ref = next((r for r in cleared if r.method == "box_tiao"), cleared[0])
        print(f"\n--- SENSITIVITY (plateau check) {cluster} / {ref.method} / {ref.basis} ---")
        print("  (ADF/half-life invariant across this grid: spread depends only on "
              "fixed weights, not lookback/sizing.)")
        for row in sensitivity(cluster, ref.basis, ref.method):
            print(f"   {row['param']:<14}={_fmt(row['value'],2):>6}  "
                  f"RT={row['round_trips']:>3}  net$={_fmt(row['net_pnl'],0):>8}  "
                  f"Sharpe={_fmt(row['sharpe'],2):>6}")
        print(f"--- COST STRESS {cluster} / {ref.method} / {ref.basis} ---")
        for row in cost_stress(cluster, ref.basis, ref.method):
            print(f"   borrow={row['borrow_bps']:>5.0f}bp  slip={row['slip_bps']:>3.0f}bp  "
                  f"RT={row['round_trips']:>3}  net$={_fmt(row['net_pnl'],0):>8}")

    if write_spec_files:
        paths = write_specs(all_rows)
        print(f"\nWrote {len(paths)} candidate specs (box_tiao, both bases) to "
              f"{SPEC_DIR}")
    return all_rows


if __name__ == "__main__":
    import sys
    run(write_spec_files=("--no-specs" not in sys.argv[1:]))
