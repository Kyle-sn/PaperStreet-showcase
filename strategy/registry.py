"""
registry.py

Name -> strategy-class registries plus a factory, so strategies can be selected
by string from config instead of by editing imports in run_live.py /
run_backtest.py. Swapping a strategy becomes a config change, not a code change.

Two families are kept separate because they consume different inputs and cannot
be used interchangeably:

  - bar strategies (BaseStrategy): consume OHLCV bars via on_bar
  - quoting strategies (BaseQuotingStrategy): consume settlement estimates via
    on_estimate (e.g. the ERCOT market maker)

Registration happens via decorator at class-definition time. strategy/__init__.py
imports every concrete strategy module so the registries are fully populated on
`import strategy`.
"""

from __future__ import annotations

from typing import Callable, TypeVar

STRATEGY_REGISTRY: dict[str, type] = {}
QUOTING_REGISTRY: dict[str, type] = {}

T = TypeVar("T", bound=type)


def _register(registry: dict[str, type], name: str | None) -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        key = name or getattr(cls, "name", "")
        if not key:
            raise ValueError(f"{cls.__name__} must define a non-empty `name` to be registered")
        if key in registry and registry[key] is not cls:
            raise ValueError(f"Strategy name {key!r} is already registered to {registry[key].__name__}")
        cls.name = key
        registry[key] = cls
        return cls

    return decorator


def register_strategy(name: str | None = None) -> Callable[[T], T]:
    """Register a BaseStrategy subclass under `name` (defaults to the class's `name` attr)."""
    return _register(STRATEGY_REGISTRY, name)


def register_quoting_strategy(name: str | None = None) -> Callable[[T], T]:
    """Register a BaseQuotingStrategy subclass under `name`."""
    return _register(QUOTING_REGISTRY, name)


def _resolve_symbols(symbols, symbol) -> list[str]:
    """Normalize the symbol universe from the canonical `symbols=` or the
    backward-compatible single `symbol=` alias into a list[str]."""
    if symbols is None:
        # Back-compat: the pre-refactor factory took a single `symbol=`.
        return [symbol] if symbol else []
    if isinstance(symbols, str):
        return [symbols]  # tolerate a bare string passed to symbols=
    return list(symbols)


def build_strategy(name: str, symbols: list[str] | None = None,
                   params: dict | None = None, *, symbol: str | None = None):
    """
    Instantiate a registered bar strategy by name.

    Parameters
    ----------
    name : str
        Registered strategy name (the `name` class attribute).
    symbols : list[str], optional
        Symbol universe this instance trades. Single-symbol strategies pass a
        one-element list (or use the `symbol=` alias below).
    params : dict, optional
        Constructor keyword arguments for the strategy.
    symbol : str, optional (keyword-only)
        Backward-compatible single-symbol alias for `symbols=[symbol]`.

    Returns
    -------
    BaseStrategy
        A configured strategy instance with `.symbols` set.
    """
    if name not in STRATEGY_REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Registered: {sorted(STRATEGY_REGISTRY)}")
    strategy = STRATEGY_REGISTRY[name](**(params or {}))
    strategy.symbols = _resolve_symbols(symbols, symbol)
    return strategy


def build_quoting_strategy(name: str, symbols: list[str] | None = None,
                           params: dict | None = None, *, symbol: str | None = None):
    """Instantiate a registered quoting strategy by name (see build_strategy for
    the `symbols=` / `symbol=` arguments)."""
    if name not in QUOTING_REGISTRY:
        raise KeyError(f"Unknown quoting strategy {name!r}. Registered: {sorted(QUOTING_REGISTRY)}")
    strategy = QUOTING_REGISTRY[name](**(params or {}))
    strategy.symbols = _resolve_symbols(symbols, symbol)
    return strategy
