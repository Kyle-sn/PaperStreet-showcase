"""
cointegration.py

Two eigenvalue-based cointegration seeders that turn a cluster's price matrix
into a candidate weight vector:

- Johansen   (statsmodels coint_johansen): the first cointegrating vector, the
  classical maximum-likelihood test for cointegration rank.
- Box-Tiao   (a small generalized-eigenvalue problem in numpy): the
  most-mean-reverting linear combination, i.e. the portfolio whose one-step
  predictability is minimal (d'Aspremont 2011).

Both usually agree closely and both land "in the right neighborhood" — in the
full pipeline they seed NES (Stage 2+). Here they are the weight estimators the
known-good harness validates.

Price basis (Open Decision #1 — LOCKED: raw prices throughout)
--------------------------------------------------------------
Resolved 2026-06-17 in RESEARCH_WORKFLOW_basket_statarb.md §6: estimate on **raw**
prices, because the basket trades fixed share quantities, so its P&L is a linear
combination of *raw* prices (`Σ wᵢ·Pᵢ`). Estimating on log prices would optimize
a different spread than the one traded, and the NES step that bridged log→raw in
the full proposal is not present in Stage 0. **Box-Tiao (raw) is the primary
vector; Johansen (raw) is the cross-check.** ADF on the spread is the real
stationarity check — do *not* lean on Johansen's own trace/eigenvalue p-values
for inference, given the raw-price deviation from the classical (log)
formulation. (We use only Johansen's eigenvector, never its test statistics.)

The `price_basis` parameter still accepts `"raw"|"log"` so the primitives stay
general for later stages, but every Stage-0 default is **raw**; pass `"log"`
explicitly only if you have a reason to revisit the locked decision.

Sign and scale convention
-------------------------
A cointegrating / mean-reverting direction is defined only up to sign and scale.
Every weight vector returned here is L2-normalized and sign-fixed so its first
component with the largest magnitude is positive. This makes weight-stability
cosine similarity (spread.weight_stability) and side-by-side comparison
meaningful; the absolute scale is irrelevant to the z-score and is set
separately as a share-quantity scale in the sim.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.tsa.vector_ar.vecm import coint_johansen


def _as_matrix(prices: pd.DataFrame, price_basis: str) -> np.ndarray:
    """Return the (T, N) price matrix in the requested basis.

    raw -> prices as-is; log -> natural log of prices. Columns are symbols,
    rows are time, ordered oldest-first (the caller's column order is preserved
    so returned weights align with `prices.columns`).
    """
    if price_basis not in ("raw", "log"):
        raise ValueError(f"price_basis must be 'raw' or 'log', got {price_basis!r}")
    x = prices.to_numpy(dtype=float)
    if price_basis == "log":
        if np.any(x <= 0):
            raise ValueError("log price_basis requires strictly positive prices")
        x = np.log(x)
    return x


def _normalize(weights: np.ndarray) -> np.ndarray:
    """L2-normalize and sign-fix so the largest-magnitude component is positive."""
    w = np.asarray(weights, dtype=float).ravel()
    norm = np.linalg.norm(w)
    if norm == 0 or not np.isfinite(norm):
        raise ValueError("degenerate (zero/non-finite) weight vector")
    w = w / norm
    # Sign-fix on the dominant component so cosine similarity across windows is
    # not flipped by the arbitrary eigenvector sign.
    if w[np.argmax(np.abs(w))] < 0:
        w = -w
    return w


def johansen_weights(prices: pd.DataFrame, price_basis: str = "raw",
                     det_order: int = 0, k_ar_diff: int = 1) -> np.ndarray:
    """First cointegrating vector from the Johansen procedure.

    The Stage-0 cross-check estimator (Box-Tiao is primary). Only the
    eigenvector is used — Johansen's trace/eigenvalue test p-values are not
    relied on for inference (Open Decision #1, raw basis).

    Parameters
    ----------
    prices : DataFrame
        (T, N) price panel, columns = symbols, oldest-first.
    price_basis : {"raw","log"}
        Estimation basis. Defaults to "raw" (Open Decision #1 — LOCKED).
    det_order : int
        Deterministic trend assumption passed to coint_johansen
        (-1 none, 0 constant, 1 linear). Constant (0) is the usual choice for
        price levels.
    k_ar_diff : int
        Number of lagged differences in the VECM.

    Returns
    -------
    np.ndarray
        Normalized, sign-fixed weight vector aligned with `prices.columns`.
    """
    x = _as_matrix(prices, price_basis)
    result = coint_johansen(x, det_order, k_ar_diff)
    # evec columns are the cointegrating vectors, ordered by descending
    # eigenvalue; the first is the most strongly mean-reverting direction.
    first_vector = result.evec[:, 0]
    return _normalize(first_vector)


def box_tiao_weights(prices: pd.DataFrame, price_basis: str = "raw") -> np.ndarray:
    """Most-mean-reverting direction via the Box-Tiao predictability eigenproblem.

    The Stage-0 **primary** weight estimator (Open Decision #1 — LOCKED to raw).
    "Most-mean-reverting" = *least* predictable: the generalized eigenvector with
    the *smallest* predictability eigenvalue (the proposal's "most-predictable
    direction" wording notwithstanding — see the eigenvalue selection below).

    Models the price vector as a VAR(1)  x_t = A x_{t-1} + e_t  (estimated by
    least squares), and measures a portfolio w's one-step predictability as

        nu(w) = (w' A Gamma A' w) / (w' Gamma w),   Gamma = cov(x_t).

    The least predictable (most mean-reverting) portfolio is the generalized
    eigenvector of (A Gamma A', Gamma) with the smallest eigenvalue. We solve it
    as the ordinary eigenproblem of Gamma^{-1} (A Gamma A') since Gamma (a
    covariance) is symmetric positive-definite.

    Parameters
    ----------
    prices : DataFrame
        (T, N) price panel, columns = symbols, oldest-first.
    price_basis : {"raw","log"}
        Estimation basis. Defaults to "raw" (Open Decision #1 — LOCKED).

    Returns
    -------
    np.ndarray
        Normalized, sign-fixed weight vector aligned with `prices.columns`.
    """
    x = _as_matrix(prices, price_basis)
    x = x - x.mean(axis=0, keepdims=True)  # center; the VAR has no intercept after this

    x_lag = x[:-1]   # x_{t-1}
    x_now = x[1:]    # x_t

    # Least-squares VAR(1): x_t' = x_{t-1}' A'  =>  A = (x_now' x_lag)(x_lag' x_lag)^{-1}
    gram = x_lag.T @ x_lag
    a = (x_now.T @ x_lag) @ np.linalg.pinv(gram)

    gamma = np.cov(x, rowvar=False)
    m = a @ gamma @ a.T

    # Generalized eigenproblem  M w = nu Gamma w. Solved directly with
    # scipy.linalg.eig(M, Gamma) rather than inverting Gamma: in a basket with a
    # near-collinear pair (e.g. GOOG/GOOGL in one cluster) Gamma is rank-deficient
    # and the Gamma^{-1} form (np.linalg.solve) raises "Singular matrix". The
    # generalized solver instead returns that zero-variance direction as the
    # least-predictable (smallest-nu) eigenvector — which is the correct answer.
    # On a well-conditioned (e.g. 2-name) basket this reduces to the same vector.
    from scipy.linalg import eig as _geneig
    eigvals, eigvecs = _geneig(m, gamma)
    eigvals = np.real(eigvals)
    eigvecs = np.real(eigvecs)
    # Guard against the infinite/NaN eigenvalues a singular Gamma can produce.
    finite = np.isfinite(eigvals)
    if not finite.any():
        raise ValueError("Box-Tiao generalized eigenproblem produced no finite eigenvalue")
    idx = np.where(finite)[0]
    least_predictable = eigvecs[:, idx[int(np.argmin(eigvals[idx]))]]
    return _normalize(least_predictable)


def estimate_weights(prices: pd.DataFrame, method: str = "box_tiao",
                     price_basis: str | None = None) -> np.ndarray:
    """Dispatch to a named estimator.

    method : {"box_tiao","johansen"}
        Defaults to "box_tiao", the Stage-0 primary vector (Open Decision #1);
        "johansen" is the cross-check.
    price_basis : override the estimator's default basis when explicitly given.
        Both default to "raw" (Open Decision #1 — LOCKED).
    """
    if method == "box_tiao":
        return box_tiao_weights(prices, price_basis=price_basis or "raw")
    if method == "johansen":
        return johansen_weights(prices, price_basis=price_basis or "raw")
    raise ValueError(f"unknown method {method!r}; expected 'box_tiao' or 'johansen'")
