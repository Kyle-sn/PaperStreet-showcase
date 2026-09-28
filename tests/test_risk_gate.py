"""
Tests for the pre-trade risk layer (risk/).

Each rule is exercised in isolation against a fabricated AccountState (no TWS,
no order handler), then the composed RiskGate and its integration with
orders/order_handler.py::place_order are covered.
"""

import time

import pytest
from unittest.mock import MagicMock
from ibapi.contract import Contract

import risk.gate
from orders.order_handler import place_order
from orders.order_types import market_order, limit_order
from risk.account_state import AccountState
from risk.gate import RiskConfig, RiskGate, build_risk_gate
from risk.rules import (
    ConnectionLivenessRule,
    DailyLossLimitRule,
    KillSwitchRule,
    OrderSizeRule,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def spy_contract():
    c = Contract()
    c.symbol = "SPY"
    c.secType = "STK"
    c.exchange = "SMART"
    c.currency = "USD"
    return c


def fresh_state(**overrides):
    """An AccountState that passes the liveness + loss-limit guards by default."""
    base = dict(
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        last_heartbeat=time.time(),
        is_connected=True,
        positions={},
    )
    base.update(overrides)
    return AccountState(**base)


# ---------------------------------------------------------------------------
# AccountState
# ---------------------------------------------------------------------------

def test_session_pnl_sums_components():
    assert fresh_state(realized_pnl=100.0, unrealized_pnl=-30.0).session_pnl == 70.0


def test_session_pnl_none_when_no_data():
    assert AccountState(realized_pnl=None, unrealized_pnl=None).session_pnl is None


def test_session_pnl_treats_missing_component_as_zero():
    assert AccountState(realized_pnl=None, unrealized_pnl=-50.0).session_pnl == -50.0


def test_reference_price_from_positions():
    state = fresh_state(positions={"SPY": {"market_price": 600.0}})
    assert state.reference_price("SPY") == 600.0
    assert state.reference_price("QQQ") is None


# ---------------------------------------------------------------------------
# OrderSizeRule
# ---------------------------------------------------------------------------

def test_order_size_under_share_cap_approves(spy_contract):
    rule = OrderSizeRule(max_shares=500)
    assert rule.check(market_order("BUY", 100), spy_contract, fresh_state()).approved


def test_order_size_over_share_cap_rejects(spy_contract):
    rule = OrderSizeRule(max_shares=500)
    decision = rule.check(market_order("BUY", 501), spy_contract, fresh_state())
    assert not decision.approved
    assert decision.rule == "order_size"


def test_order_size_notional_cap_uses_limit_price(spy_contract):
    rule = OrderSizeRule(max_shares=None, max_notional=50_000)
    # 100 * 600 = 60_000 > 50_000
    decision = rule.check(limit_order("BUY", 100, 600.0), spy_contract, fresh_state())
    assert not decision.approved


def test_order_size_notional_cap_uses_reference_price_for_market_order(spy_contract):
    rule = OrderSizeRule(max_shares=None, max_notional=50_000)
    state = fresh_state(positions={"SPY": {"market_price": 600.0}})
    decision = rule.check(market_order("SELL", 100), spy_contract, state)
    assert not decision.approved


def test_order_size_notional_not_enforced_when_no_price(spy_contract):
    # Market order, no held position -> no resolvable price -> cap skipped (not fail-closed).
    rule = OrderSizeRule(max_shares=None, max_notional=1.0)
    assert rule.check(market_order("BUY", 100), spy_contract, fresh_state()).approved


# ---------------------------------------------------------------------------
# KillSwitchRule
# ---------------------------------------------------------------------------

def test_kill_switch_off_approves(spy_contract):
    assert KillSwitchRule().check(market_order("BUY", 1), spy_contract, fresh_state()).approved


def test_kill_switch_tripped_rejects(spy_contract):
    rule = KillSwitchRule()
    rule.trip("manual halt")
    decision = rule.check(market_order("BUY", 1), spy_contract, fresh_state())
    assert not decision.approved
    assert rule.tripped


def test_kill_switch_armed_at_construction(spy_contract):
    rule = KillSwitchRule(tripped=True)
    assert not rule.check(market_order("BUY", 1), spy_contract, fresh_state()).approved


def test_kill_switch_reset(spy_contract):
    rule = KillSwitchRule(tripped=True)
    rule.reset()
    assert rule.check(market_order("BUY", 1), spy_contract, fresh_state()).approved


# ---------------------------------------------------------------------------
# ConnectionLivenessRule
# ---------------------------------------------------------------------------

def test_liveness_fresh_heartbeat_approves(spy_contract):
    rule = ConnectionLivenessRule(max_staleness_seconds=420)
    assert rule.check(market_order("BUY", 1), spy_contract, fresh_state()).approved


def test_liveness_stale_heartbeat_rejects(spy_contract):
    rule = ConnectionLivenessRule(max_staleness_seconds=420)
    state = fresh_state(last_heartbeat=time.time() - 1000)
    decision = rule.check(market_order("BUY", 1), spy_contract, state)
    assert not decision.approved
    assert decision.rule == "connection_liveness"


def test_liveness_no_heartbeat_rejects(spy_contract):
    rule = ConnectionLivenessRule(max_staleness_seconds=420)
    assert not rule.check(market_order("BUY", 1), spy_contract, fresh_state(last_heartbeat=None)).approved


def test_liveness_disconnected_rejects(spy_contract):
    rule = ConnectionLivenessRule(max_staleness_seconds=420)
    assert not rule.check(market_order("BUY", 1), spy_contract, fresh_state(is_connected=False)).approved


def test_liveness_now_fn_injectable(spy_contract):
    # Heartbeat at t=1000, "now" at t=1100, threshold 60 -> stale.
    rule = ConnectionLivenessRule(max_staleness_seconds=60, now_fn=lambda: 1100.0)
    state = fresh_state(last_heartbeat=1000.0)
    assert not rule.check(market_order("BUY", 1), spy_contract, state).approved


# ---------------------------------------------------------------------------
# DailyLossLimitRule
# ---------------------------------------------------------------------------

def test_loss_limit_above_limit_approves(spy_contract):
    rule = DailyLossLimitRule(loss_limit=-5_000)
    state = fresh_state(realized_pnl=-1_000.0, unrealized_pnl=-500.0)
    assert rule.check(market_order("BUY", 1), spy_contract, state).approved
    assert not rule.tripped


def test_loss_limit_breach_rejects_and_latches(spy_contract):
    rule = DailyLossLimitRule(loss_limit=-5_000)
    state = fresh_state(realized_pnl=-4_000.0, unrealized_pnl=-1_500.0)  # -5_500
    decision = rule.check(market_order("BUY", 1), spy_contract, state)
    assert not decision.approved
    assert rule.tripped


def test_loss_limit_stays_tripped_after_recovery(spy_contract):
    """The critical latching property: once tripped, a later heartbeat showing
    recovered PnL must NOT un-trip the breaker."""
    rule = DailyLossLimitRule(loss_limit=-5_000)
    rule.check(market_order("BUY", 1), spy_contract, fresh_state(realized_pnl=-6_000.0))
    assert rule.tripped
    # PnL has since recovered well into the black.
    recovered = fresh_state(realized_pnl=10_000.0)
    assert not rule.check(market_order("BUY", 1), spy_contract, recovered).approved
    assert rule.tripped


def test_loss_limit_reset(spy_contract):
    rule = DailyLossLimitRule(loss_limit=-5_000)
    rule.check(market_order("BUY", 1), spy_contract, fresh_state(realized_pnl=-6_000.0))
    rule.reset()
    assert not rule.tripped
    assert rule.check(market_order("BUY", 1), spy_contract, fresh_state()).approved


def test_loss_limit_approves_when_pnl_unavailable(spy_contract):
    rule = DailyLossLimitRule(loss_limit=-5_000)
    state = AccountState(realized_pnl=None, unrealized_pnl=None, last_heartbeat=time.time())
    assert rule.check(market_order("BUY", 1), spy_contract, state).approved


# ---------------------------------------------------------------------------
# RiskGate composition
# ---------------------------------------------------------------------------

def test_gate_approves_when_all_rules_pass(spy_contract):
    gate = build_risk_gate()
    assert gate.check(market_order("BUY", 100), spy_contract, fresh_state()).approved


def test_gate_first_rejection_wins(spy_contract):
    # Kill switch is first in order; even a size-violating order reports kill_switch.
    gate = build_risk_gate(RiskConfig(kill_switch_active=True, max_order_shares=1))
    decision = gate.check(market_order("BUY", 10_000), spy_contract, fresh_state())
    assert not decision.approved
    assert decision.rule == "kill_switch"


def test_gate_trip_kill_switch_halts(spy_contract):
    gate = build_risk_gate()
    assert gate.check(market_order("BUY", 100), spy_contract, fresh_state()).approved
    gate.trip_kill_switch("test halt")
    assert not gate.check(market_order("BUY", 100), spy_contract, fresh_state()).approved


def test_gate_trip_kill_switch_alerts(monkeypatch):
    alerts = []
    monkeypatch.setattr(risk.gate, "send_alert", lambda kind, msg: alerts.append((kind, msg)))
    gate = build_risk_gate()

    gate.trip_kill_switch("position reconciliation divergence for SPY")

    assert alerts == [("kill_switch", "position reconciliation divergence for SPY")]


def test_gate_trip_kill_switch_does_not_alert_if_no_kill_switch_rule(monkeypatch):
    alerts = []
    monkeypatch.setattr(risk.gate, "send_alert", lambda kind, msg: alerts.append((kind, msg)))
    gate = RiskGate([])  # no KillSwitchRule composed in

    gate.trip_kill_switch("unreachable")

    assert alerts == []


def test_gate_exposes_stateful_rules(spy_contract):
    gate = build_risk_gate()
    assert isinstance(gate.kill_switch, KillSwitchRule)
    assert isinstance(gate.loss_limit, DailyLossLimitRule)


def test_gate_loss_limit_latches_across_calls(spy_contract):
    gate = build_risk_gate(RiskConfig(daily_loss_limit=-5_000))
    breach = fresh_state(realized_pnl=-6_000.0)
    assert not gate.check(market_order("BUY", 1), spy_contract, breach).approved
    # Recovered PnL, still halted.
    assert not gate.check(market_order("BUY", 1), spy_contract, fresh_state()).approved


# ---------------------------------------------------------------------------
# place_order integration
# ---------------------------------------------------------------------------

@pytest.fixture
def app_with_id():
    from ib_app import IBApp
    app = IBApp()
    app.nextValidId(1)
    app.placeOrder = MagicMock()
    return app


def test_place_order_submits_when_gate_approves(app_with_id, spy_contract):
    gate = build_risk_gate()
    db_id = place_order(app_with_id, spy_contract, market_order("BUY", 100),
                        risk_gate=gate, account_state=fresh_state())
    app_with_id.placeOrder.assert_called_once()
    assert db_id != -1


def test_place_order_blocks_when_gate_rejects(app_with_id, spy_contract):
    gate = build_risk_gate()
    gate.trip_kill_switch("test")
    db_id = place_order(app_with_id, spy_contract, market_order("BUY", 100),
                        risk_gate=gate, account_state=fresh_state())
    app_with_id.placeOrder.assert_not_called()
    assert db_id == -1


def test_place_order_rejection_does_not_consume_order_id(app_with_id, spy_contract):
    gate = build_risk_gate()
    gate.trip_kill_switch("test")
    before = app_with_id.nextOrderId
    place_order(app_with_id, spy_contract, market_order("BUY", 100),
                risk_gate=gate, account_state=fresh_state())
    assert app_with_id.nextOrderId == before  # id not burned on a rejected order


def test_place_order_without_gate_is_unchanged(app_with_id, spy_contract):
    # Back-compat: no risk_gate -> original behavior, order submitted.
    place_order(app_with_id, spy_contract, market_order("BUY", 100))
    app_with_id.placeOrder.assert_called_once()
