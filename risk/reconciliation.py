"""
reconciliation.py

The daily position-reconciliation check (DEPLOYMENT.md §6.3): compare what
IBKR reports we hold against what our own recorded fills imply we hold.
Position-only for now -- cash reconciliation would need a full local cash
ledger (starting balance + every fill's proceeds - commissions), which
doesn't exist yet (see ROADMAP.md).

This is a genuinely independent check, not a re-check of the same broker
data: `get_position()` already just relays IBKR's own broker-confirmed
value, so comparing it to itself would be meaningless. `get_recorded_net_position()`
is instead derived purely from our own `executions` rows, so a divergence
here means the two records of history disagree -- a missed fill, a bust, a
corporate action (split/reverse split/merger), or manual intervention in TWS.
"""

from __future__ import annotations

from dataclasses import dataclass

from database import trading as _tdb


@dataclass
class ReconciliationResult:
    symbol: str
    broker_position: float
    recorded_position: float

    @property
    def divergence(self) -> float:
        return self.broker_position - self.recorded_position

    @property
    def ok(self) -> bool:
        return self.divergence == 0


def reconcile_position(symbol: str, broker_position: float) -> ReconciliationResult:
    """Compare `broker_position` (caller-supplied, from a live broker callback)
    against the net signed sum of our own recorded executions for `symbol`."""
    recorded_position = _tdb.get_recorded_net_position(symbol)
    return ReconciliationResult(
        symbol=symbol,
        broker_position=broker_position,
        recorded_position=recorded_position,
    )
