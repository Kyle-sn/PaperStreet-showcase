"""
Tests for risk/reconciliation.py and run_live.py's daily reconciliation wiring
(DEPLOYMENT.md §6.3). No TWS connection needed -- `database/trading.py`'s
executions table is the only input, and run_daily_reconciliation() is exercised
against a fake session/risk_gate.
"""

import pytest

from database import trading as tdb
from risk.reconciliation import reconcile_position


def _record_execution(symbol, side, shares, order_ib_id=1):
    order_id = tdb.save_order(symbol=symbol, action="BUY" if side == "BOT" else "SELL",
                              order_type="MKT", quantity=shares, ib_order_id=order_ib_id)
    tdb.save_execution(symbol=symbol, side=side, shares=shares, price=100.0,
                       executed_at="2026-09-25T14:30:00.000Z", ib_exec_id=f"{order_ib_id}-{side}-{shares}",
                       order_id=order_id)
    return order_id


# ---------------------------------------------------------------------------
# reconcile_position
# ---------------------------------------------------------------------------

def test_reconcile_position_ok_when_matching(temp_db):
    _record_execution("SPY", "BOT", 65)
    result = reconcile_position("SPY", broker_position=65.0)
    assert result.ok
    assert result.divergence == 0
    assert result.recorded_position == 65.0


def test_reconcile_position_detects_divergence(temp_db):
    _record_execution("SPY", "BOT", 65)
    # IBKR reports fewer shares than our fills imply -- e.g. a missed SELL fill.
    result = reconcile_position("SPY", broker_position=40.0)
    assert not result.ok
    assert result.divergence == -25.0


def test_reconcile_position_nets_multiple_fills(temp_db):
    _record_execution("SPY", "BOT", 65, order_ib_id=1)
    _record_execution("SPY", "SLD", 25, order_ib_id=2)
    result = reconcile_position("SPY", broker_position=40.0)
    assert result.ok
    assert result.recorded_position == 40.0


def test_reconcile_position_no_recorded_fills(temp_db):
    # Flat per our own records but IBKR reports a position -- e.g. a pre-existing
    # position this system never traded.
    result = reconcile_position("QQQ", broker_position=999.0)
    assert not result.ok
    assert result.recorded_position == 0.0
    assert result.divergence == 999.0


def test_reconcile_position_ignores_other_symbols(temp_db):
    _record_execution("SPY", "BOT", 65)
    result = reconcile_position("QQQ", broker_position=0.0)
    assert result.ok


# ---------------------------------------------------------------------------
# run_live.py::run_daily_reconciliation
# ---------------------------------------------------------------------------

class FakeSessionForReconciliation:
    def __init__(self, position):
        self._position = position

    def get_position(self, symbol):
        return self._position


class FakeRiskGate:
    def __init__(self):
        self.tripped_reason = None

    def trip_kill_switch(self, reason="manual"):
        self.tripped_reason = reason


def test_run_daily_reconciliation_does_not_trip_on_match(temp_db):
    from run_live import run_daily_reconciliation
    _record_execution("SPY", "BOT", 65)
    risk_gate = FakeRiskGate()

    result = run_daily_reconciliation(FakeSessionForReconciliation(65.0), "SPY", risk_gate)

    assert result.ok
    assert risk_gate.tripped_reason is None


def test_run_daily_reconciliation_trips_kill_switch_on_divergence(temp_db):
    from run_live import run_daily_reconciliation
    _record_execution("SPY", "BOT", 65)
    risk_gate = FakeRiskGate()

    result = run_daily_reconciliation(FakeSessionForReconciliation(40.0), "SPY", risk_gate)

    assert not result.ok
    assert risk_gate.tripped_reason is not None
    assert "SPY" in risk_gate.tripped_reason
