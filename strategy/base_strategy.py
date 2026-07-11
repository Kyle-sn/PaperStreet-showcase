"""
base_strategy.py

The contract every bar-driven strategy implements. It is the single interface
the backtest engine and the live loop both consume — anything that satisfies it
is plug-and-play in both, with no surrounding code changes.

A strategy is a pure signal generator:
1. Receives completed bars one time step at a time (on_bars)
2. Maintains its own internal state (indicators)
3. Returns OrderRequest objects — it never places orders itself

Scope
-----
An instance trades a *list* of symbols; single-symbol is the N=1 case (see
docs/MULTI_STRATEGY_REFACTOR.md). The general entry point is `on_bars`, which
receives one bar per symbol and returns the orders to submit. Single-symbol
strategies need only implement `on_bar` — the base class dispatches `on_bars`
to it when `len(symbols) == 1`, so a single-name strategy carries no per-symbol
bookkeeping. Multi-symbol strategies override `on_bars` directly.

Lifecycle hooks (on_start/on_stop/on_fill) default to no-ops so a strategy
overrides only what it needs.
"""

from __future__ import annotations

from abc import ABC

from strategy.signal import OrderRequest
from strategy.symbols import SymbolsMixin


class BaseStrategy(SymbolsMixin, ABC):
    """
    Abstract base class for bar-driven trading strategies.

    Concrete strategies must define a unique `name` (used for DB tagging and
    registry lookup) and implement either `on_bar` (single-symbol, the common
    case) or `on_bars` (multi-symbol).

    Attributes
    ----------
    name : str
        Unique identifier. Set by the @register_strategy decorator, or as a
        class attribute.
    symbols : list[str]
        Symbol universe this instance trades. Set by the registry factory at
        build time; defaults to [] for directly-constructed instances. The
        single-symbol convenience `self.symbol` is provided by SymbolsMixin.
    """

    name: str = ""

    # ------------------------------------------------------------------
    # Strategy interface — override on_bar (single-symbol) or on_bars (multi)
    # ------------------------------------------------------------------

    def on_bars(self, bars: dict[str, dict],
                positions: dict[str, float]) -> list[OrderRequest] | None:
        """
        Process one time step across the symbol universe and return orders.

        This is the general N-symbol entry point the engine and live loop call.
        The default implementation handles the single-symbol case by dispatching
        to `on_bar`, so a single-name strategy implements only `on_bar` while a
        multi-symbol strategy overrides this method.

        Parameters
        ----------
        bars : dict[str, dict]
            One bar per symbol this instance trades, keyed by symbol. Each bar is
            a dict with keys datetime, open, high, low, close, volume (see
            docs/DATA_MODEL.md).
        positions : dict[str, float]
            Current net position per symbol, keyed by symbol (broker-confirmed
            live, Portfolio.position in backtest). Missing symbols default to 0.

        Returns
        -------
        list[OrderRequest]
            Orders to submit this step; an empty list (or None) for no action.
            Build each with self.buy()/self.sell() so symbol and strategy are
            tagged for you.
        """
        sym = self.symbol  # raises if multi-symbol and on_bars was not overridden
        order = self.on_bar(bars[sym], positions.get(sym, 0.0))
        return [order] if order is not None else []

    def on_bar(self, bar: dict, position: float = 0.0) -> OrderRequest | None:
        """
        Single-symbol convenience entry. Override this for the common single-name
        case; the base `on_bars` dispatches here when the instance trades one
        symbol. Multi-symbol strategies override `on_bars` and may leave this
        unimplemented.

        Parameters
        ----------
        bar : dict
            One time step of market data (see docs/DATA_MODEL.md).
        position : float
            Current net position in the traded symbol. Passing position rather
            than self-tracking avoids inventory drift when a signal is rejected
            downstream.

        Returns
        -------
        OrderRequest | None
            An order to submit, or None for no action.
        """
        raise NotImplementedError(
            "Implement on_bar (single-symbol) or override on_bars (multi-symbol)."
        )

    # ------------------------------------------------------------------
    # Order construction helpers (auto-tag symbol + strategy name)
    # ------------------------------------------------------------------

    def buy(self, quantity: float, order_type: str = "MKT",
            limit_price: float | None = None, tif: str = "DAY",
            symbol: str | None = None) -> OrderRequest:
        return OrderRequest(
            action="BUY", quantity=quantity, order_type=order_type,
            limit_price=limit_price, tif=tif,
            symbol=self.symbol if symbol is None else symbol, strategy=self.name,
        )

    def sell(self, quantity: float, order_type: str = "MKT",
             limit_price: float | None = None, tif: str = "DAY",
             symbol: str | None = None) -> OrderRequest:
        return OrderRequest(
            action="SELL", quantity=quantity, order_type=order_type,
            limit_price=limit_price, tif=tif,
            symbol=self.symbol if symbol is None else symbol, strategy=self.name,
        )

    # ------------------------------------------------------------------
    # Lifecycle hooks — override as needed; default to no-ops
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """Called once when the strategy starts. Load warm-up history here."""

    def on_stop(self) -> None:
        """Called once when the strategy stops. Clean up state here."""

    def on_fill(self, action: str, quantity: float, price: float) -> None:
        """Called when an execution for this strategy is confirmed."""
