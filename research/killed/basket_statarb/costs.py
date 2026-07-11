"""
costs.py

The research-tier cost model for the daily spread sim. Mirrors the conventions
in docs/BACKTESTING.md and the PaperStreet SimBroker (backtesting/broker.py) so
the research numbers are in the same units as the Stage-1 engine that is the
real gate — but this is a standalone, dependency-light model, not a reuse of the
engine.

Four cost components, each per leg of the basket:

1. Commission   IBKR equities model: max(commission_min, per_share * |shares|)
                per leg, per side (entry and exit each pay). Same defaults as
                SimBroker: $0.005/share, $1.00 minimum.
2. Slippage     Half the bid-ask spread, charged as half_spread_bps of the fill
                notional per leg per side. Conservative; large-caps are near 0.
3. Borrow       Short legs accrue borrow = borrow_bps_annual * short_notional *
                days_held / DAY_COUNT. Flat annualized rate (no per-name HTB
                curve at research tier).
4. Dividends    SIGNED per-leg dividend cash over the hold: long legs RECEIVE
   (overlay)     the dividend, short legs PAY it. See the DIVIDEND TREATMENT
                 note below — this is Open Decision #5.

DIVIDEND TREATMENT (Open Decision #5 — signed per-leg overlay; Kyle 2026-06-17)
------------------------------------------------------------------------------
A fixed-share basket realizes dividends as real cash flows: you receive the
dividend on every share you are long and owe it (to the stock-loan lender) on
every share you are short. Earlier this model charged shorts only, at a single
basket-wide yield — which cannot represent a basket whose legs have *different*
yields (e.g. Cluster B money-center banks at 2–4% on both sides). Per Kyle's
2026-06-17 decision the overlay is now a **signed per-leg flat-yield cash flow**:

    overlay = Σ_i  leg_shares_i · leg_price_i · yield_i · days / DIVIDEND_DAY_COUNT

`leg_shares_i` carries sign (long > 0, short < 0), so long legs add cash and
short legs subtract it; per-leg `yield_i` lets dispersed-yield baskets net
correctly. The overlay is a *credit* to PnL when long-leg dividends exceed the
short-leg dividends, hence `holding_cost = borrow − overlay` (see holding_cost).

BASIS COUPLING (which the sim enforces, not this model):
  - Under **TRADES** (split-only cash prices) the overlay is **ON** — dividends
    are real cash the price path does not contain.
  - Under **ADJUSTED_LAST** (total-return) the overlay is **OFF** — dividends are
    already reinvested into the price path; charging them again double-counts.
The sim passes per-leg yields only on the TRADES basis and `None` (→ uniform
`dividend_yield_annual_bps`, default 0) on ADJUSTED_LAST.

Still an approximation (Open Decision #5 not fully closed): a *flat* annual yield,
not an ex-date schedule. Per-leg yields are hand-entered Stage-0 estimates; the
faithful version (actual ex-date cash flows on a split-only series) is Stage 1.
"""

from __future__ import annotations

from dataclasses import dataclass

# Borrow accrual uses a 360-day year (money-market convention for stock-loan
# financing). Dividend accrual uses a 365-day calendar year (dividends are quoted
# as a calendar-annual yield). Both documented so the annualized inputs are
# unambiguous.
DAY_COUNT = 360.0
DIVIDEND_DAY_COUNT = 365.0


@dataclass
class CostModel:
    """Per-leg cost parameters for the daily spread sim.

    commission_per_share, commission_min : IBKR equities commission (SimBroker
        defaults). Charged per leg, per side.
    half_spread_bps : half the bid-ask spread, in basis points of notional,
        charged per leg per side as slippage.
    borrow_bps_annual : flat annualized borrow rate on short notional.
    dividend_yield_annual_bps : uniform fallback annual yield (bps) used for any
        leg without an explicit per-leg yield. Keep 0 under ADJUSTED_LAST to avoid
        double-counting; the sim passes explicit per-leg yields under TRADES (see
        module docstring, Open Decision #5).
    """

    commission_per_share: float = 0.005
    commission_min: float = 1.0
    half_spread_bps: float = 1.0
    borrow_bps_annual: float = 50.0
    dividend_yield_annual_bps: float = 0.0

    # ------------------------------------------------------------------
    # Per-leg, per-side instantaneous costs (commission + slippage)
    # ------------------------------------------------------------------

    def commission(self, shares: float) -> float:
        """IBKR commission for one leg fill: max(min, per_share * |shares|)."""
        shares = abs(float(shares))
        if shares == 0:
            return 0.0
        return max(self.commission_min, self.commission_per_share * shares)

    def slippage(self, shares: float, price: float) -> float:
        """Half-spread slippage for one leg fill, in dollars."""
        notional = abs(float(shares) * float(price))
        return self.half_spread_bps / 10_000.0 * notional

    def entry_exit_cost(self, shares_per_leg, prices_per_leg) -> float:
        """Total commission + slippage for one *side* (all legs) of a round trip.

        `shares_per_leg` and `prices_per_leg` are equal-length sequences of the
        signed share quantity and the fill price for each leg.
        """
        total = 0.0
        for shares, price in zip(shares_per_leg, prices_per_leg):
            total += self.commission(shares) + self.slippage(shares, price)
        return total

    # ------------------------------------------------------------------
    # Holding-period accruals (borrow + dividends on shorts)
    # ------------------------------------------------------------------

    def short_notional(self, shares_per_leg, prices_per_leg) -> float:
        """Sum of |shares * price| over the legs that are short (shares < 0)."""
        total = 0.0
        for shares, price in zip(shares_per_leg, prices_per_leg):
            if shares < 0:
                total += abs(shares * price)
        return total

    def borrow_cost(self, short_notional: float, days_held: float) -> float:
        """Borrow accrual on short notional over the hold (flat annualized bps)."""
        return self.borrow_bps_annual / 10_000.0 * short_notional * days_held / DAY_COUNT

    def dividend_overlay(self, shares_per_leg, prices_per_leg, days_held: float,
                         leg_yields=None) -> float:
        """Signed net dividend cash over the hold (long receives +, short pays −).

        overlay = Σ_i shares_i · price_i · yield_i · days / DIVIDEND_DAY_COUNT,
        with shares_i carrying sign. `leg_yields` is a per-leg sequence of annual
        yield *fractions* aligned to the legs; any leg left None falls back to the
        uniform `dividend_yield_annual_bps`. Returns 0 when every yield is 0
        (the ADJUSTED_LAST path — overlay OFF, see module docstring).
        """
        uniform = self.dividend_yield_annual_bps / 10_000.0
        total = 0.0
        for i, (shares, price) in enumerate(zip(shares_per_leg, prices_per_leg)):
            y = uniform if leg_yields is None else float(leg_yields[i])
            total += float(shares) * float(price) * y * days_held / DIVIDEND_DAY_COUNT
        return total

    def holding_cost(self, shares_per_leg, prices_per_leg, days_held: float,
                     leg_yields=None) -> float:
        """Net financing cost for one round trip's hold: borrow − dividend overlay.

        Borrow accrues on short notional (always a cost); the dividend overlay is
        signed (long legs receive, short legs pay). The result is therefore signed
        too — negative when long-leg dividends exceed borrow plus short-leg
        dividends — and keeps the sim identity net = gross − entry − exit − holding.
        """
        sn = self.short_notional(shares_per_leg, prices_per_leg)
        borrow = self.borrow_cost(sn, days_held)
        overlay = self.dividend_overlay(shares_per_leg, prices_per_leg, days_held,
                                        leg_yields=leg_yields)
        return borrow - overlay
