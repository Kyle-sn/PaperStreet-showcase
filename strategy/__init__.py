"""
strategy package

Importing this package populates the strategy registries by importing every
concrete strategy module. Select strategies by name via
strategy.registry.build_strategy / build_quoting_strategy rather than importing
concrete classes directly.
"""

from strategy.base_strategy import BaseStrategy
from strategy.base_quoting_strategy import BaseQuotingStrategy
from strategy.signal import OrderRequest
from strategy.registry import (
    STRATEGY_REGISTRY,
    QUOTING_REGISTRY,
    register_strategy,
    register_quoting_strategy,
    build_strategy,
    build_quoting_strategy,
)

# Import concrete strategies so their @register decorators run on package import.
# (This public mirror omits strategy/in_progress/ — strategies not yet killed or
# shipped are kept in the private working repo.)
from strategy import benchmarks  # noqa: F401
from strategy.parked import spy_short_reversal  # noqa: F401

__all__ = [
    "BaseStrategy",
    "BaseQuotingStrategy",
    "OrderRequest",
    "STRATEGY_REGISTRY",
    "QUOTING_REGISTRY",
    "register_strategy",
    "register_quoting_strategy",
    "build_strategy",
    "build_quoting_strategy",
]
