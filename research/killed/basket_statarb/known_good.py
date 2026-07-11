"""
known_good.py

Stage-0 known-good validation harness: prove the machinery before pointing it at
real candidate clusters. Runs the full primitive pipeline on a basket that
*should* score well (GOOG / GOOGL — dual-class, mechanically cointegrated)
against several economically-unrelated pairs that *should* be rejected, and
prints the readouts side by side.

This validates STATISTICAL DETECTION, not tradeability. GOOG/GOOGL is a tight
spread, so a modest or even cost-negative sim PnL on it is NOT a machinery
failure — the screens (low ADF p, half-life in band, stable weights) are what
must separate it from the unrelated pairs. Tradeable amplitude is what the real
candidate clusters (a later, separate prompt) will test.

No gates here. No real candidate clusters. Open Decisions #2-#4 (IS/OOS
boundary, cluster list, numeric pass/fail thresholds) are now LOCKED (workflow
§6) but they govern the *candidate one-shot* (Steps 4-7, a separate later task),
not this known-good harness — so no gate is applied here. Weights use the locked
raw basis (#1): Box-Tiao primary, Johansen raw cross-check.

Run
---
    # offline, from cache (after a one-time prewarm):
    python -m research.killed.basket_statarb.known_good

    # prewarm the needed symbols first (requires TWS on paper port 7497):
    python -m research.killed.basket_statarb.known_good --prewarm
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np

from research.killed.basket_statarb import cointegration, data, spread
from research.killed.basket_statarb.costs import CostModel
from research.killed.basket_statarb.sim import simulate

# Known-good dual-class basket and economically-unrelated controls.
KNOWN_GOOD = ("GOOG", "GOOGL")
UNRELATED = [
    ("DUK", "REGN"),   # utility vs biotech
    ("KO", "NVDA"),    # beverage vs semiconductors
    ("XOM", "BIIB"),   # energy vs biotech
]

ALL_SYMBOLS = sorted({s for pair in [KNOWN_GOOD, *UNRELATED] for s in pair})

# Both estimators on raw prices (Open Decision #1 — LOCKED: raw throughout).
# Box-Tiao is the primary vector; Johansen (raw) is the cross-check — listed in
# that order.
METHODS = [
    ("box_tiao", "raw"),
    ("johansen", "raw"),
]

LOOKBACK = 60
SIM_KW = dict(z_enter=2.0, z_exit=0.5, z_stop=3.5, time_stop_mult=3.0,
              lookback=LOOKBACK, gross_notional=10_000.0)


@dataclass
class PairMethodReadout:
    pair: str
    method: str
    basis: str
    n_obs: int
    adf_p: float
    half_life: float
    weight_stability: float
    weights: np.ndarray
    sim_trades: int
    sim_net_pnl: float
    sim_gross_pnl: float
    sim_costs: float
    sim_win_rate: float


def _fit_weights(closes, method: str, basis: str) -> np.ndarray:
    return cointegration.estimate_weights(closes, method=method, price_basis=basis)


def evaluate_pair(sym_a: str, sym_b: str) -> list[PairMethodReadout]:
    closes, opens = data.load_pair_panels(sym_a, sym_b)
    pair = f"{sym_a}/{sym_b}"
    readouts: list[PairMethodReadout] = []

    # Weight-stability: refit on two adjacent halves and compare directions.
    half = len(closes) // 2
    first_half, second_half = closes.iloc[:half], closes.iloc[half:]

    for method, basis in METHODS:
        weights = _fit_weights(closes, method, basis)
        spr = spread.build_spread(closes, weights)
        diag = spread.diagnose(spr, lookback=LOOKBACK)

        try:
            w1 = _fit_weights(first_half, method, basis)
            w2 = _fit_weights(second_half, method, basis)
            stability = spread.weight_stability(w1, w2)
        except Exception:  # noqa: BLE001 — degenerate sub-window; report NaN
            stability = float("nan")

        sim = simulate(closes, opens, weights, cost_model=CostModel(), **SIM_KW)

        readouts.append(PairMethodReadout(
            pair=pair, method=method, basis=basis, n_obs=diag.n_obs,
            adf_p=diag.adf_pvalue, half_life=diag.half_life,
            weight_stability=stability, weights=weights,
            sim_trades=sim.n_trades, sim_net_pnl=sim.net_pnl,
            sim_gross_pnl=sim.gross_pnl, sim_costs=sim.total_costs,
            sim_win_rate=sim.win_rate,
        ))
    return readouts


def _fmt(x: float, nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x))):
        return "   nan"
    if isinstance(x, float) and np.isinf(x):
        return "   inf"
    return f"{x:.{nd}f}"


def print_readouts(rows: list[PairMethodReadout]) -> None:
    header = (f"{'pair':<12} {'method':<9} {'basis':<5} {'n':>5} "
              f"{'ADF p':>8} {'half-life':>10} {'wstab':>7} "
              f"{'trades':>7} {'net$':>10} {'gross$':>10} {'costs$':>9} {'win%':>6}")
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r.pair:<12} {r.method:<9} {r.basis:<5} {r.n_obs:>5} "
              f"{_fmt(r.adf_p, 4):>8} {_fmt(r.half_life, 1):>10} "
              f"{_fmt(r.weight_stability, 3):>7} {r.sim_trades:>7} "
              f"{_fmt(r.sim_net_pnl, 0):>10} {_fmt(r.sim_gross_pnl, 0):>10} "
              f"{_fmt(r.sim_costs, 0):>9} "
              f"{_fmt(100 * r.sim_win_rate, 0) if not np.isnan(r.sim_win_rate) else '   nan':>6}")


def run() -> list[PairMethodReadout]:
    all_rows: list[PairMethodReadout] = []

    print("\n=== KNOWN-GOOD (expect: low ADF p, half-life ~2-15d, stable weights) ===")
    kg = evaluate_pair(*KNOWN_GOOD)
    print_readouts(kg)
    all_rows.extend(kg)

    print("\n=== UNRELATED CONTROLS (expect: high ADF p and/or unstable weights) ===")
    control_rows: list[PairMethodReadout] = []
    for pair in UNRELATED:
        control_rows.extend(evaluate_pair(*pair))
    print_readouts(control_rows)
    all_rows.extend(control_rows)

    print("\nNote: this validates statistical *detection*. GOOG/GOOGL is a tight "
          "spread, so a small/negative sim net$ on it is not a failure -- the ADF "
          "p, half-life and weight-stability separation is. Tradeable amplitude "
          "is what the real candidate clusters will test (separate later task).")
    return all_rows


if __name__ == "__main__":
    if "--prewarm" in sys.argv[1:]:
        print(f"Prewarming {len(ALL_SYMBOLS)} symbols via TWS: {ALL_SYMBOLS}")
        data.prewarm(ALL_SYMBOLS)
    run()
