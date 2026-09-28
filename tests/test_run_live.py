"""
Tests for run_live.py's connection-loss recovery (DEPLOYMENT.md §6.2).

No real TWS/Gateway connection: session and order_app are simple fakes with
controllable is_connected/isConnected() state, and sleep_fn is injected so
tests run instantly rather than actually backing off.
"""

import pytest

import run_live
from run_live import RECONNECT_BACKOFF_SECONDS, reconnect_with_backoff


class FakeSession:
    def __init__(self, connected=True):
        self.is_connected = connected
        self.reconnect_calls = 0

    def reconnect(self):
        self.reconnect_calls += 1
        self.is_connected = True


class FakeOrderApp:
    def __init__(self, connected=True):
        self._connected = connected

    def isConnected(self):
        return self._connected


def _recording_sleep(delays):
    def _sleep(seconds):
        delays.append(seconds)
    return _sleep


def test_returns_immediately_when_already_connected():
    session = FakeSession(connected=True)
    order_app = FakeOrderApp(connected=True)
    delays = []

    result = reconnect_with_backoff(session, order_app, sleep_fn=_recording_sleep(delays))

    assert result is order_app
    assert delays == []
    assert session.reconnect_calls == 0


def test_reconnects_session_when_only_session_is_down():
    session = FakeSession(connected=False)
    order_app = FakeOrderApp(connected=True)
    delays = []

    result = reconnect_with_backoff(session, order_app, sleep_fn=_recording_sleep(delays))

    assert result is order_app
    assert session.reconnect_calls == 1
    assert session.is_connected is True
    assert delays == [RECONNECT_BACKOFF_SECONDS[0]]


def test_replaces_order_app_when_only_orders_connection_is_down():
    session = FakeSession(connected=True)
    dead_order_app = FakeOrderApp(connected=False)
    fresh_order_app = FakeOrderApp(connected=True)
    delays = []

    result = reconnect_with_backoff(
        session, dead_order_app,
        connect_orders_fn=lambda: fresh_order_app,
        sleep_fn=_recording_sleep(delays),
    )

    assert result is fresh_order_app
    assert session.reconnect_calls == 0
    assert delays == [RECONNECT_BACKOFF_SECONDS[0]]


def test_backoff_increases_then_holds_at_the_last_value():
    # Both fail to reconnect for several attempts (reconnect()/connect_orders_fn
    # keep raising) before finally succeeding, so the delay schedule is exercised
    # past its length.
    session = FakeSession(connected=False)
    calls = {"n": 0}
    attempts_before_success = len(RECONNECT_BACKOFF_SECONDS) + 1

    def flaky_reconnect():
        calls["n"] += 1
        if calls["n"] < attempts_before_success:
            raise ConnectionError("still down")
        session.is_connected = True

    session.reconnect = flaky_reconnect
    order_app = FakeOrderApp(connected=True)
    delays = []

    reconnect_with_backoff(session, order_app, sleep_fn=_recording_sleep(delays))

    expected = [
        RECONNECT_BACKOFF_SECONDS[min(i, len(RECONNECT_BACKOFF_SECONDS) - 1)]
        for i in range(attempts_before_success)
    ]
    assert delays == expected
    assert delays[-1] == RECONNECT_BACKOFF_SECONDS[-1]


def test_a_failed_reconnect_attempt_does_not_raise():
    session = FakeSession(connected=False)
    order_app = FakeOrderApp(connected=True)
    delays = []

    # Fails on the first attempt (must not crash the caller), succeeds on
    # the second so the test terminates.
    attempts = {"n": 0}

    def reconnect_then_succeed():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("refused")
        session.is_connected = True

    session.reconnect = reconnect_then_succeed

    result = reconnect_with_backoff(session, order_app, sleep_fn=_recording_sleep(delays))

    assert result is order_app
    assert attempts["n"] == 2
    assert delays == [RECONNECT_BACKOFF_SECONDS[0], RECONNECT_BACKOFF_SECONDS[1]]


# ---------------------------------------------------------------------------
# reconnect_with_backoff: stuck-reconnect alerting (DEPLOYMENT.md 7.3)
# ---------------------------------------------------------------------------

def test_no_alert_for_a_transient_blip_that_clears_before_max_backoff(monkeypatch):
    alerts = []
    monkeypatch.setattr(run_live, "send_alert", lambda kind, msg: alerts.append((kind, msg)))

    session = FakeSession(connected=False)
    calls = {"n": 0}

    def reconnect_then_succeed():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("refused")
        session.is_connected = True

    session.reconnect = reconnect_then_succeed
    order_app = FakeOrderApp(connected=True)

    reconnect_with_backoff(session, order_app, sleep_fn=_recording_sleep([]))

    assert alerts == []


def test_alerts_once_reconnect_gets_stuck_at_max_backoff(monkeypatch):
    alerts = []
    monkeypatch.setattr(run_live, "send_alert", lambda kind, msg: alerts.append((kind, msg)))

    session = FakeSession(connected=False)
    # Never succeeds within the first max_backoff_index+2 attempts, so the
    # loop holds at max backoff for a couple of iterations before recovering.
    max_backoff_index = len(RECONNECT_BACKOFF_SECONDS) - 1
    attempts_before_success = max_backoff_index + 3
    calls = {"n": 0}

    def flaky_reconnect():
        calls["n"] += 1
        if calls["n"] < attempts_before_success:
            raise ConnectionError("still down")
        session.is_connected = True

    session.reconnect = flaky_reconnect
    order_app = FakeOrderApp(connected=True)

    reconnect_with_backoff(session, order_app, sleep_fn=_recording_sleep([]))

    assert [kind for kind, _ in alerts] == ["reconnect_stuck"]
