"""
basis_diagnostic.py  (Stage-0, §0 of the candidate-IS prompt — REPORT ONLY)

Settles an open question the desktop review raised: the workflow's data basis is
``what_to_show=ADJUSTED_LAST`` (total-return), but a fixed-share basket trades
split-only ``TRADES`` prices plus dividend cash. ADJUSTED_LAST injects a
non-tradeable per-leg total-return drift into the *level* spread. GOOG/GOOGL is
the clean probe: zero dividends, so ANY TRADES-vs-ADJUSTED_LAST difference here
is the adjustment artifact alone (the 0.50 -> 0.99 ratio "seam"), not dividends.

This runs the known-good GOOG/GOOGL pair through the SAME validated primitives
(cointegration / spread / sim, raw price_basis, locked Decision #1) on BOTH
``what_to_show`` values and prints them side by side, plus the raw price-ratio
path on each. It does NOT pick a basis — that is Kyle's lock decision (§0 / #1).

Run (offline, after GOOG/GOOGL are cached on both bases):
    python -m research.killed.basket_statarb.basis_diagnostic
"""

from __future__ import annotations

import numpy as np

from research.killed.basket_statarb import cointegration, data, spread
from research.killed.basket_statarb.costs import CostModel
from research.killed.basket_statarb.sim import simulate

PAIR = ("GOOG", "GOOGL")
WHAT_TO_SHOW = ["TRADES", "ADJUSTED_LAST"]
METHODS = [("box_tiao", "raw"), ("johansen", "raw")]
LOOKBACK = 60
# GOOG/GOOGL pays no dividend, so the dividend overlay is moot here regardless of
# basis: dividend_yield_annual_bps=0 is correct on BOTH bases for this pair.
SIM_KW = dict(z_enter=2.0, z_exit=0.5, z_stop=3.5, time_stop_mult=3.0,
              lookback=LOOKBACK, gross_notional=10_000.0)


def _fmt(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "   nan"
    if isinstance(x, float) and np.isinf(x):
        return "   inf"
    return f"{x:.{nd}f}"


def ratio_path(closes):
    """Summarize the raw GOOG/GOOGL close ratio path (the '0.50 -> 0.99 seam')."""
    r = (closes[PAIR[0]] / closes[PAIR[1]]).to_numpy(dtype=float)
    return dict(first=r[0], last=r[-1], min=r.min(), max=r.max(),
                mean=float(np.mean(r)), std=float(np.std(r)))


def run():
    print(f"\n=== §0 BASIS DIAGNOSTIC — {PAIR[0]}/{PAIR[1]} "
          f"(zero-dividend probe; report, do not pick) ===\n")

    # Raw price-ratio path on each basis (the desktop-review question).
    print("--- raw close-ratio path  (GOOG / GOOGL) ---")
    print(f"{'what_to_show':<15}{'first':>8}{'last':>8}{'min':>8}{'max':>8}"
          f"{'mean':>8}{'std':>8}{'n':>7}")
    panels_by_wts = {}
    for wts in WHAT_TO_SHOW:
        closes, opens = data.load_pair_panels(*PAIR, what_to_show=wts)
        panels_by_wts[wts] = (closes, opens)
        rp = ratio_path(closes)
        print(f"{wts:<15}{rp['first']:>8.3f}{rp['last']:>8.3f}{rp['min']:>8.3f}"
              f"{rp['max']:>8.3f}{rp['mean']:>8.3f}{rp['std']:>8.3f}{len(closes):>7}")

    # Screens + sim on each basis x method.
    print("\n--- screens + research-tier sim (raw price_basis; same SIM_KW) ---")
    header = (f"{'what_to_show':<15}{'method':<9}{'n':>6}{'ADF p':>8}"
              f"{'half-life':>11}{'wstab':>8}{'trades':>8}{'net$':>9}"
              f"{'gross$':>9}{'costs$':>9}{'win%':>6}")
    print(header)
    print("-" * len(header))
    for wts in WHAT_TO_SHOW:
        closes, opens = panels_by_wts[wts]
        half = len(closes) // 2
        first_half, second_half = closes.iloc[:half], closes.iloc[half:]
        for method, basis in METHODS:
            w = cointegration.estimate_weights(closes, method=method, price_basis=basis)
            spr = spread.build_spread(closes, w)
            diag = spread.diagnose(spr, lookback=LOOKBACK)
            try:
                w1 = cointegration.estimate_weights(first_half, method=method, price_basis=basis)
                w2 = cointegration.estimate_weights(second_half, method=method, price_basis=basis)
                wstab = spread.weight_stability(w1, w2)
            except Exception:
                wstab = float("nan")
            sim = simulate(closes, opens, w, cost_model=CostModel(), **SIM_KW)
            print(f"{wts:<15}{method:<9}{diag.n_obs:>6}{_fmt(diag.adf_pvalue, 4):>8}"
                  f"{_fmt(diag.half_life, 1):>11}{_fmt(wstab, 3):>8}{sim.n_trades:>8}"
                  f"{_fmt(sim.net_pnl, 0):>9}{_fmt(sim.gross_pnl, 0):>9}"
                  f"{_fmt(sim.total_costs, 0):>9}"
                  f"{_fmt(100 * sim.win_rate, 0) if not np.isnan(sim.win_rate) else '  nan':>6}")
            # Report the per-leg weight vector so the (-1,+1) hedge is visible.
            print(f"{'':<15}{'  weights=':<9}"
                  f"{PAIR[0]}:{w[0]:+.3f}  {PAIR[1]}:{w[1]:+.3f}")

    print("\nNOTE: GOOG/GOOGL pays no dividend, so dividend overlay is OFF on both "
          "bases here by construction — the only TRADES-vs-ADJUSTED_LAST difference "
          "is the adjustment artifact. Report for Kyle's basis lock; not a pick.")


if __name__ == "__main__":
    run()
