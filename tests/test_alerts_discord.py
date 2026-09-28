"""
Tests for alerts/discord.py (see docs/DEPLOYMENT.md 7.3).

No real network calls: urllib.request.urlopen is monkeypatched, and the
webhook URL is supplied directly (via _get_webhook_url) rather than through a
real AWS SSM round-trip -- that resolution path belongs to the VM, not to a
unit test. tests/conftest.py's autouse `_no_real_discord_alerts` fixture makes
every OTHER test's send_alert() calls no-ops; these tests override it locally
to exercise the real send path.
"""

import pytest

import alerts.discord as discord_module
from alerts.discord import send_alert


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """`_last_sent` is module-level state shared across calls within a process;
    isolate each test from whatever the previous one left behind."""
    discord_module._last_sent.clear()
    yield
    discord_module._last_sent.clear()


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_no_webhook_configured_does_not_raise(monkeypatch):
    monkeypatch.setattr(discord_module, "_get_webhook_url", lambda: None)
    send_alert("kind", "message")  # must not raise


def test_sends_expected_payload_to_the_webhook_url(monkeypatch):
    monkeypatch.setattr(discord_module, "_get_webhook_url", lambda: "https://discord.example/webhook")
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data, request.headers, timeout))
        return _FakeResponse()

    monkeypatch.setattr(discord_module.urllib.request, "urlopen", fake_urlopen)

    send_alert("kill_switch", "position reconciliation divergence for SPY")

    assert len(calls) == 1
    url, body, headers, timeout = calls[0]
    assert url == "https://discord.example/webhook"
    assert b"kill_switch" in body
    assert b"position reconciliation divergence for SPY" in body
    assert headers["Content-type"] == "application/json"
    assert timeout == discord_module.REQUEST_TIMEOUT_SECONDS


def test_repeat_alert_within_window_is_suppressed(monkeypatch):
    monkeypatch.setattr(discord_module, "_get_webhook_url", lambda: "https://discord.example/webhook")
    calls = []
    monkeypatch.setattr(discord_module.urllib.request, "urlopen",
                        lambda request, timeout: calls.append(1) or _FakeResponse())

    send_alert("kind", "first", min_interval_s=300)
    send_alert("kind", "second", min_interval_s=300)

    assert len(calls) == 1


def test_different_kinds_are_not_rate_limited_against_each_other(monkeypatch):
    monkeypatch.setattr(discord_module, "_get_webhook_url", lambda: "https://discord.example/webhook")
    calls = []
    monkeypatch.setattr(discord_module.urllib.request, "urlopen",
                        lambda request, timeout: calls.append(1) or _FakeResponse())

    send_alert("kind_a", "message", min_interval_s=300)
    send_alert("kind_b", "message", min_interval_s=300)

    assert len(calls) == 2


def test_a_failed_send_does_not_raise(monkeypatch):
    monkeypatch.setattr(discord_module, "_get_webhook_url", lambda: "https://discord.example/webhook")

    def raising_urlopen(request, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(discord_module.urllib.request, "urlopen", raising_urlopen)

    send_alert("kind", "message")  # must not raise
