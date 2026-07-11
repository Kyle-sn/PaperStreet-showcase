"""
risk/

System-wide, pre-trade risk enforcement. The single place where an order is
vetted before it reaches IBKR.

`orders/order_handler.py::place_order` calls `RiskGate.check(order, contract,
account_state)` before `app.placeOrder`. The gate runs a list of isolated,
individually-testable rules and returns the first rejection (or an approval).
See `docs/RISK.md` for the controls this layer enforces and their parameters.
"""

from risk.account_state import AccountState
from risk.gate import RiskConfig, RiskGate, build_risk_gate
from risk.rules import (
    ConnectionLivenessRule,
    DailyLossLimitRule,
    KillSwitchRule,
    OrderSizeRule,
    RiskDecision,
    RiskRule,
)

__all__ = [
    "AccountState",
    "RiskConfig",
    "RiskGate",
    "build_risk_gate",
    "RiskDecision",
    "RiskRule",
    "OrderSizeRule",
    "KillSwitchRule",
    "ConnectionLivenessRule",
    "DailyLossLimitRule",
]
