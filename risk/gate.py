"""
gate.py

`RiskGate` — the single pre-trade checkpoint. `orders/order_handler.py::
place_order` calls `gate.check(order, contract, account_state)` before
`app.placeOrder`; the order is submitted only on approval.

The gate composes the isolated rules in `rules.py` and runs them in order,
returning the FIRST rejection (and logging it). No rule logic is inlined here or
in `place_order` — the gate only sequences rules and surfaces their decision, so
each rule stays independently testable. See `docs/RISK.md`.

Rule order (first reject wins): kill switch → connection liveness → daily loss
limit → order size. Liveness precedes the loss limit so the loss limit only
evaluates fresh PnL.
"""

from __future__ import annotations

from dataclasses import dataclass

from risk.rules import (
    ConnectionLivenessRule,
    DailyLossLimitRule,
    KillSwitchRule,
    OrderSizeRule,
    RiskDecision,
)
from utils.log_config import setup_logger

logger = setup_logger(__name__)


@dataclass
class RiskConfig:
    """Tunable risk limits.

    Defaults are sized for the paper-trading deployable capital track (see
    `docs/RISK.md` → Capital Tracks). They are deliberately conservative
    pre-live placeholder values and MUST be reviewed before a live account:

    - `daily_loss_limit` is a drawdown-budget breaker — the binding constraint
      that keeps equity comfortably above the track's floor.
    - `max_order_shares` / `max_order_notional` are fat-finger ceilings sized
      well above a single entry's typical notional, not tight operating limits.
    - `max_staleness_seconds` sits above IBKR's update cadence so a
      quiet-but-live connection is not falsely tripped.

    Real values are set in the private working repo; the numbers below are
    illustrative placeholders, not the actual deployed limits.
    """

    max_order_shares: float | None = 1_000.0
    max_order_notional: float | None = 100_000.0
    max_staleness_seconds: float = 420.0
    daily_loss_limit: float = -10_000.0
    kill_switch_active: bool = False


class RiskGate:
    """Runs a list of `RiskRule`s; first rejection wins and is logged."""

    def __init__(self, rules):
        self._rules = list(rules)

    def check(self, order, contract, account_state) -> RiskDecision:
        for rule in self._rules:
            decision = rule.check(order, contract, account_state)
            if not decision.approved:
                logger.warning(
                    f"RiskGate REJECT|symbol={getattr(contract, 'symbol', '?')}"
                    f"|action={getattr(order, 'action', '?')}"
                    f"|qty={getattr(order, 'totalQuantity', '?')}"
                    f"|rule={decision.rule}|reason={decision.reason}"
                )
                return decision
        return RiskDecision.approve()

    def rule(self, name: str):
        """Return the composed rule with `name`, or None. Lets callers reach the
        stateful rules (e.g. to trip/reset the kill switch or loss limit)."""
        for rule in self._rules:
            if getattr(rule, "name", None) == name:
                return rule
        return None

    @property
    def kill_switch(self) -> KillSwitchRule | None:
        return self.rule(KillSwitchRule.name)

    @property
    def loss_limit(self) -> DailyLossLimitRule | None:
        return self.rule(DailyLossLimitRule.name)

    def trip_kill_switch(self, reason: str = "manual") -> None:
        ks = self.kill_switch
        if ks is not None:
            ks.trip(reason)


def build_risk_gate(config: RiskConfig | None = None) -> RiskGate:
    """Assemble the default gate from a `RiskConfig`."""
    config = config or RiskConfig()
    rules = [
        KillSwitchRule(tripped=config.kill_switch_active),
        ConnectionLivenessRule(config.max_staleness_seconds),
        DailyLossLimitRule(config.daily_loss_limit),
        OrderSizeRule(config.max_order_shares, config.max_order_notional),
    ]
    return RiskGate(rules)
