"""
symbols.py

Shared symbol-set machinery for both strategy families (bar and quoting).

A strategy instance now trades a *list* of symbols; single-symbol is the N=1
case (see docs/MULTI_STRATEGY_REFACTOR.md → Decisions 1-4). `symbols` is the
canonical attribute, set by the registry factory at build time.

`symbol` is a backward-compatible convenience for the single-symbol case: it
reads `symbols[0]`, returns "" when nothing is set (matching the old default for
directly-constructed instances), and raises when the instance trades several
symbols (there is no single symbol to return). This keeps existing single-symbol
strategy bodies — and `self.buy()` / `self.sell()`, which tag orders with
`self.symbol` — working unchanged.
"""

from __future__ import annotations


class SymbolsMixin:
    """Provides the `symbols` list and the single-symbol `symbol` convenience.

    Mixed into both BaseStrategy and BaseQuotingStrategy so the two families
    share one symbol contract rather than duplicating (and drifting) it — the
    refactor's "generalize, don't bifurcate" principle.
    """

    # Immutable default is safe as a class attribute (no shared-mutable footgun).
    # The registry replaces it per instance via the `symbols`/`symbol` setters,
    # which assign a fresh tuple — the class default is never mutated in place.
    _symbols: tuple[str, ...] = ()

    @property
    def symbols(self) -> list[str]:
        """The symbol universe this instance trades. Canonical; set at build time."""
        return list(self._symbols)

    @symbols.setter
    def symbols(self, value) -> None:
        self._symbols = tuple(value)

    @property
    def symbol(self) -> str:
        """The single traded symbol — convenience for single-symbol strategies.

        Returns "" when no symbol is set (the old default for directly-constructed
        instances) and raises when the instance trades several: there is no one
        symbol to return, so multi-symbol callers must use `symbols` (or pass an
        explicit `symbol=` to buy()/sell()).
        """
        if len(self._symbols) == 1:
            return self._symbols[0]
        if not self._symbols:
            return ""
        raise AttributeError(
            f"self.symbol is ambiguous for a multi-symbol strategy "
            f"(symbols={list(self._symbols)}); use self.symbols, or pass symbol= "
            f"to buy()/sell()."
        )

    @symbol.setter
    def symbol(self, value: str) -> None:
        # Back-compat: older code (and the pre-refactor registry) set a single
        # symbol via `.symbol = "SPY"`. Map it onto the symbols list.
        self._symbols = (value,) if value else ()
