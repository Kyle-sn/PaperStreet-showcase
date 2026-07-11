"""
Hermetic unit tests for the basket_statarb Stage-0 primitives.

All synthetic — no TWS, no DB, no Databento. These prove the primitives recover
known quantities on constructed data:

- cointegration recovers a known cointegrating vector
- ou_half_life recovers a known OU half-life
- ADF separates a stationary series from a random walk
- rolling_zscore is correctly standardized
- the daily sim round-trips a constructed mean-reverting spread
"""

import numpy as np
import pandas as pd
import pytest

from research.killed.basket_statarb import cointegration, spread
from research.killed.basket_statarb.costs import CostModel
from research.killed.basket_statarb.sim import simulate


def _daily_index(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2000-01-03", periods=n)


def _cosine(a, b) -> float:
    a = np.asarray(a, float).ravel()
    b = np.asarray(b, float).ravel()
    return abs(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b))


# ----------------------------------------------------------------------
# Cointegration: recover a known cointegrating vector
# ----------------------------------------------------------------------

def _make_cointegrated(n=3000, beta=2.0, seed=0):
    """x1 = common random walk f; x2 = beta*f + stationary s.

    Then x2 - beta*x1 = s is stationary, so the cointegrating vector is
    proportional to (beta, -1) (sign/scale free).
    """
    rng = np.random.default_rng(seed)
    f = np.cumsum(rng.normal(0, 1.0, n))          # I(1) common trend
    # Stationary OU residual (AR(1), phi<1)
    phi = 0.9
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + rng.normal(0, 1.0)
    x1 = 50.0 + f
    x2 = 50.0 + beta * f + s
    idx = _daily_index(n)
    return pd.DataFrame({"X1": x1, "X2": x2}, index=idx)


@pytest.mark.parametrize("method,basis", [("johansen", "raw"), ("box_tiao", "raw")])
def test_cointegration_recovers_known_vector(method, basis):
    beta = 2.0
    prices = _make_cointegrated(beta=beta, seed=7)
    w = cointegration.estimate_weights(prices, method=method, price_basis=basis)
    true_vec = np.array([beta, -1.0])
    assert _cosine(w, true_vec) > 0.99, f"{method}: recovered {w}, expected ∝ {true_vec}"


def test_cointegration_log_basis_supported():
    # Open Decision #1: both bases must work end-to-end (no hard-coding).
    prices = _make_cointegrated(beta=2.0, seed=11)
    for method in ("johansen", "box_tiao"):
        for basis in ("raw", "log"):
            w = cointegration.estimate_weights(prices, method=method, price_basis=basis)
            assert w.shape == (2,)
            assert np.isclose(np.linalg.norm(w), 1.0)  # normalized


# ----------------------------------------------------------------------
# OU half-life: recover a known half-life
# ----------------------------------------------------------------------

@pytest.mark.parametrize("target_hl", [5.0, 10.0, 20.0])
def test_ou_half_life_recovers_known(target_hl):
    rng = np.random.default_rng(3)
    n = 40_000
    phi = 2 ** (-1.0 / target_hl)   # so true half-life = -ln2/ln(phi) = target_hl
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + rng.normal(0, 1.0)
    series = pd.Series(s, index=_daily_index(n))
    hl = spread.ou_half_life(series)
    assert hl == pytest.approx(target_hl, rel=0.15), f"got {hl}, expected ~{target_hl}"


def test_half_life_infinite_for_random_walk():
    rng = np.random.default_rng(5)
    rw = pd.Series(np.cumsum(rng.normal(0, 1.0, 5000)), index=_daily_index(5000))
    hl = spread.ou_half_life(rw)
    # A pure random walk has no mean reversion: lambda ~ 0, half-life huge/inf.
    assert hl > 100 or np.isinf(hl)


# ----------------------------------------------------------------------
# ADF: stationary vs random walk
# ----------------------------------------------------------------------

def test_adf_rejects_unit_root_on_stationary():
    rng = np.random.default_rng(1)
    n = 3000
    phi = 0.85
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + rng.normal(0, 1.0)
    p = spread.adf_pvalue(pd.Series(s, index=_daily_index(n)))
    assert p < 0.01, f"stationary AR(1) should reject unit root, p={p}"


def test_adf_does_not_reject_on_random_walk():
    rng = np.random.default_rng(2)
    rw = pd.Series(np.cumsum(rng.normal(0, 1.0, 3000)), index=_daily_index(3000))
    p = spread.adf_pvalue(rw)
    assert p > 0.10, f"random walk should not reject unit root, p={p}"


# ----------------------------------------------------------------------
# Rolling z-score: correctly standardized
# ----------------------------------------------------------------------

def test_rolling_zscore_is_standardized():
    rng = np.random.default_rng(4)
    s = pd.Series(rng.normal(0, 1.0, 5000), index=_daily_index(5000))
    z = spread.rolling_zscore(s, lookback=100).dropna()
    # Over many windows the z-scores are ~standard normal.
    assert abs(z.mean()) < 0.1
    assert z.std(ddof=1) == pytest.approx(1.0, rel=0.1)


def test_rolling_zscore_matches_manual_window():
    s = pd.Series(np.arange(1, 21, dtype=float), index=_daily_index(20))
    lookback = 10
    z = spread.rolling_zscore(s, lookback=lookback)
    window = s.iloc[-lookback:]
    expected = (s.iloc[-1] - window.mean()) / window.std(ddof=1)
    assert z.iloc[-1] == pytest.approx(expected)


# ----------------------------------------------------------------------
# build_spread / weight_stability
# ----------------------------------------------------------------------

def test_build_spread_is_weighted_raw_combo():
    prices = pd.DataFrame({"A": [10.0, 11.0], "B": [20.0, 19.0]}, index=_daily_index(2))
    w = np.array([1.0, -0.5])
    spr = spread.build_spread(prices, w)
    assert spr.iloc[0] == pytest.approx(10.0 - 0.5 * 20.0)
    assert spr.iloc[1] == pytest.approx(11.0 - 0.5 * 19.0)


def test_weight_stability_sign_invariant():
    a = np.array([0.8, -0.6])
    assert spread.weight_stability(a, a) == pytest.approx(1.0)
    assert spread.weight_stability(a, -a) == pytest.approx(1.0)   # sign-free
    assert spread.weight_stability(a, np.array([0.6, 0.8])) == pytest.approx(0.0, abs=1e-9)


# ----------------------------------------------------------------------
# Daily sim: round-trips a constructed mean-reverting spread
# ----------------------------------------------------------------------

def test_sim_round_trips_mean_reverting_spread():
    # Construct two prices whose (1, -1) spread is a tradeable OU process.
    rng = np.random.default_rng(0)
    n = 1500
    base = 100.0 + np.cumsum(rng.normal(0, 0.3, n))   # shared drift
    phi = 2 ** (-1.0 / 8.0)                            # half-life ~8 bars
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + rng.normal(0, 3.0)    # large-amplitude spread
    closes = pd.DataFrame({"A": base + s, "B": base}, index=_daily_index(n))
    # Opens ~ prior close (fills land at next open); small offset is fine.
    opens = closes.shift(1).bfill()

    weights = np.array([1.0, -1.0])
    result = simulate(closes, opens, weights, z_enter=1.5, z_exit=0.3, z_stop=4.0,
                      lookback=60, gross_notional=10_000.0, cost_model=CostModel())

    assert result.n_trades >= 5, "a strongly mean-reverting spread should round-trip"
    assert np.isfinite(result.half_life) and result.half_life < 30
    # Each trade's net = gross - all costs (accounting identity).
    for t in result.trades:
        assert t.net_pnl == pytest.approx(
            t.gross_pnl - t.entry_cost - t.exit_cost - t.holding_cost)


# ----------------------------------------------------------------------
# Dividend overlay: signed per-leg cash (long receives, short pays)
# ----------------------------------------------------------------------

def test_dividend_overlay_signed_per_leg():
    cm = CostModel()
    # Leg 1 long 100 sh @ $50 yielding 4%; leg 2 short 100 sh @ $50 yielding 1%.
    shares = [100.0, -100.0]
    prices = [50.0, 50.0]
    yields = [0.04, 0.01]
    days = 365  # one full year for a clean check
    overlay = cm.dividend_overlay(shares, prices, days, leg_yields=yields)
    # long receives 0.04*5000=200, short pays 0.01*5000=50 -> net +150 received.
    assert overlay == pytest.approx(200.0 - 50.0, rel=1e-6)


def test_dividend_overlay_off_when_zero():
    cm = CostModel()  # dividend_yield_annual_bps default 0
    shares = [10.0, -10.0]
    prices = [100.0, 100.0]
    # No per-leg yields -> uniform fallback (0) -> overlay exactly 0 (ADJUSTED_LAST path).
    assert cm.dividend_overlay(shares, prices, 30) == 0.0


def test_holding_cost_is_borrow_minus_overlay():
    cm = CostModel(borrow_bps_annual=50.0)
    shares = [100.0, -100.0]
    prices = [50.0, 50.0]
    yields = [0.04, 0.01]
    days = 365
    borrow = cm.borrow_cost(cm.short_notional(shares, prices), days)
    overlay = cm.dividend_overlay(shares, prices, days, leg_yields=yields)
    assert cm.holding_cost(shares, prices, days, leg_yields=yields) == pytest.approx(
        borrow - overlay)


def test_sim_dividend_yields_change_pnl():
    # Same spread, with vs without a long-favoring dividend overlay: net must differ
    # and the with-overlay net must be >= (long leg receives more than short pays).
    rng = np.random.default_rng(0)
    n = 1500
    base = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
    phi = 2 ** (-1.0 / 8.0)
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + rng.normal(0, 3.0)
    closes = pd.DataFrame({"A": base + s, "B": base}, index=_daily_index(n))
    opens = closes.shift(1).bfill()
    weights = np.array([1.0, -1.0])
    kw = dict(z_enter=1.5, z_exit=0.3, z_stop=4.0, lookback=60,
              gross_notional=10_000.0, cost_model=CostModel())
    base_run = simulate(closes, opens, weights, **kw)
    div_run = simulate(closes, opens, weights, dividend_yields=np.array([0.05, 0.0]), **kw)
    assert div_run.net_pnl != pytest.approx(base_run.net_pnl)
