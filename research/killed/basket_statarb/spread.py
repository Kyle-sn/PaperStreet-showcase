"""
spread.py

The spread series and its statistics — the reusable primitives the harness
screens candidates on and the sim trades.

Key convention (matches the proposal): the spread is the weighted combination
in *raw* prices with *fixed share quantities*. Weights are also estimated on raw
prices (Open Decision #1 — LOCKED), so estimation and trading are in the same
space; the traded spread is always raw prices @ weights, because we hold fixed
shares and never rebalance.

All time-based statistics (half-life, z-score lookback) are in *trading days*
(bars), not wall-clock — overnight gaps must not pose as fast reversion. Since
this is the daily layer, one bar = one trading day.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller


def build_spread(prices: pd.DataFrame, weights: np.ndarray) -> pd.Series:
    """Weighted combination of raw prices: spread_t = sum_i w_i * price_{i,t}.

    `weights` aligns positionally with `prices.columns`. Returns a Series
    indexed like `prices`.
    """
    w = np.asarray(weights, dtype=float).ravel()
    if w.shape[0] != prices.shape[1]:
        raise ValueError(
            f"weights length {w.shape[0]} != number of price columns {prices.shape[1]}")
    return pd.Series(prices.to_numpy(dtype=float) @ w, index=prices.index, name="spread")


def rolling_zscore(spread: pd.Series, lookback: int = 60, min_periods: int | None = None) -> pd.Series:
    """Rolling z-score: (spread - rolling_mean) / rolling_std over `lookback` bars.

    Uses only trailing data (the window ending at each bar), so the value at bar
    t is known at t's close — no lookahead. Bars before the window fills are NaN.
    """
    if min_periods is None:
        min_periods = lookback
    roll = spread.rolling(window=lookback, min_periods=min_periods)
    mean = roll.mean()
    std = roll.std(ddof=1)
    z = (spread - mean) / std
    return z.rename("zscore")


def adf_pvalue(spread: pd.Series, maxlag: int | None = None,
               regression: str = "c") -> float:
    """Augmented Dickey-Fuller p-value for the spread (H0: unit root).

    Low p-value => reject unit root => evidence of stationarity / mean reversion.
    `regression="c"` includes a constant (a spread with non-zero mean).
    """
    s = pd.Series(spread).dropna().to_numpy(dtype=float)
    if s.size < 10:
        return float("nan")
    return float(adfuller(s, maxlag=maxlag, regression=regression, autolag="AIC")[1])


def adf_stat(spread: pd.Series, maxlag: int | None = None,
             regression: str = "c") -> float:
    """ADF test statistic (more negative => stronger rejection of the unit root)."""
    s = pd.Series(spread).dropna().to_numpy(dtype=float)
    if s.size < 10:
        return float("nan")
    return float(adfuller(s, maxlag=maxlag, regression=regression, autolag="AIC")[0])


def ou_half_life(spread: pd.Series) -> float:
    """Mean-reversion half-life (in bars) from a discrete OU / AR(1) fit.

    Regress the one-step change on the lagged level:

        delta_s_t = c + lambda * s_{t-1} + e_t

    For a mean-reverting series lambda < 0, and the continuous-time half-life is

        half_life = -ln(2) / ln(1 + lambda).

    Returns +inf when the fit is non-mean-reverting (lambda >= 0) — i.e. no
    finite half-life, which the half-life band screen reads as a reject.
    """
    s = pd.Series(spread).dropna().to_numpy(dtype=float)
    if s.size < 3:
        return float("nan")
    s_lag = s[:-1]
    delta = s[1:] - s_lag
    # OLS of delta on [1, s_lag]
    design = np.column_stack([np.ones_like(s_lag), s_lag])
    coef, *_ = np.linalg.lstsq(design, delta, rcond=None)
    lam = coef[1]
    if lam >= 0:
        return float("inf")
    decay = 1.0 + lam
    if decay <= 0:
        # Oscillatory/over-damped AR(1); no meaningful half-life.
        return float("inf")
    return float(-np.log(2.0) / np.log(decay))


def weight_stability(weights_a: np.ndarray, weights_b: np.ndarray) -> float:
    """Cosine similarity between two cointegrating vectors, sign-invariant.

    Returns |cos(angle)| in [0, 1]; ~1 means the basket's direction barely moved
    between adjacent fit windows (stable), low means it churned. Absolute value
    is used because the cointegrating direction is defined only up to sign.
    """
    a = np.asarray(weights_a, dtype=float).ravel()
    b = np.asarray(weights_b, dtype=float).ravel()
    if a.shape != b.shape:
        raise ValueError(f"weight vectors differ in length: {a.shape} vs {b.shape}")
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return float("nan")
    return float(abs(np.dot(a, b)) / denom)


@dataclass
class SpreadDiagnostics:
    """Bundle of the screen statistics for one spread series."""
    adf_pvalue: float
    adf_stat: float
    half_life: float
    zscore_std: float
    zscore_last: float
    n_obs: int


def diagnose(spread: pd.Series, lookback: int = 60) -> SpreadDiagnostics:
    """Compute the standard screen bundle for a spread in one call."""
    z = rolling_zscore(spread, lookback=lookback)
    return SpreadDiagnostics(
        adf_pvalue=adf_pvalue(spread),
        adf_stat=adf_stat(spread),
        half_life=ou_half_life(spread),
        zscore_std=float(z.std(ddof=1)),
        zscore_last=float(z.dropna().iloc[-1]) if z.notna().any() else float("nan"),
        n_obs=int(pd.Series(spread).dropna().size),
    )
