"""
base_quoting_strategy.py

The contract for two-sided *quoting* strategies — a distinct family from the
bar-driven BaseStrategy. A quoting strategy continuously prices a market (bid
and offer) around some fair value rather than emitting discrete BUY/SELL signals
off OHLCV bars. The ERCOT market maker is the first member.

It is kept separate (rather than forced into on_bars) because the input and the
output differ fundamentally: it consumes settlement *estimates* and returns
*quote* dicts, not OHLCV bars and OrderRequests. Sharing one interface would
contort both. The two families are generalized to multi-symbol together (see
docs/MULTI_STRATEGY_REFACTOR.md → Decision 4) so the system never carries one
generalized and one un-generalized interface.
"""

from __future__ import annotations

from abc import ABC
from datetime import datetime
from typing import Optional

from strategy.symbols import SymbolsMixin


class BaseQuotingStrategy(SymbolsMixin, ABC):
    """
    Abstract base class for settlement/estimate-driven quoting strategies.

    An instance quotes a *list* of symbols; single-symbol is the N=1 case. The
    general entry point is `on_estimates` (one estimate per symbol → one quote
    per symbol); single-symbol strategies implement only `on_estimate` and the
    base dispatches to it.

    Attributes
    ----------
    name : str
        Unique identifier, set by @register_quoting_strategy or as a class attr.
    symbols : list[str]
        Symbol universe this instance quotes (provided by SymbolsMixin, with the
        single-symbol convenience `self.symbol`).
    """

    name: str = ""

    def on_estimates(self, estimates: dict[str, object], positions: dict[str, float],
                     as_of: Optional[datetime] = None) -> dict[str, dict]:
        """
        Process new fair-value estimates across the symbol universe and return a
        two-sided quote per symbol.

        General N-symbol entry point. The default implementation handles the
        single-symbol case by dispatching to `on_estimate`; multi-symbol quoting
        strategies override this method.

        The return is a per-symbol *mapping* (not a list) because a quote dict
        carries no symbol of its own — keying it externally by symbol mirrors the
        `estimates` input and leaves the documented quote-dict shape untouched
        (docs/MULTI_STRATEGY_REFACTOR.md → Open Decision #2, resolved).

        Parameters
        ----------
        estimates : dict[str, object]
            Latest estimate per symbol, keyed by symbol. Each estimate's shape is
            strategy-specific (e.g. RollingEstimate).
        positions : dict[str, float]
            Current net inventory per symbol, keyed by symbol. Missing symbols
            default to 0.
        as_of : datetime, optional
            Shared wall-clock time for staleness checks across all symbols.
            Defaults to now; pass event time in backtests.

        Returns
        -------
        dict[str, dict]
            Quote dict per symbol, keyed by symbol. A symbol is omitted when its
            quote is suppressed (its single-symbol on_estimate returned None).
        """
        sym = self.symbol  # raises if multi-symbol and on_estimates was not overridden
        quote = self.on_estimate(estimates[sym], positions.get(sym, 0.0), as_of)
        return {sym: quote} if quote is not None else {}

    def on_estimate(self, estimate, position: float = 0.0,
                    as_of: Optional[datetime] = None) -> Optional[dict]:
        """
        Single-symbol convenience entry. Override this for the common single-name
        case; the base `on_estimates` dispatches here when the instance quotes one
        symbol. Multi-symbol strategies override `on_estimates` instead.

        Parameters
        ----------
        estimate : object
            Latest estimate (e.g. RollingEstimate). Shape is strategy-specific.
        position : float
            Current net inventory. Injected by the caller; not self-tracked.
        as_of : datetime, optional
            Wall-clock time for staleness checks. Defaults to now; pass event
            time in backtests.

        Returns
        -------
        dict | None
            A quote dict, or None when quoting is suppressed (stale data,
            inventory cap, etc.).
        """
        raise NotImplementedError(
            "Implement on_estimate (single-symbol) or override on_estimates "
            "(multi-symbol)."
        )

    # ------------------------------------------------------------------
    # Lifecycle hooks — override as needed; default to no-ops
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """Called once when the strategy starts."""

    def on_stop(self) -> None:
        """Called once when the strategy stops."""
