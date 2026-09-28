import sys
import os

import pytest
from decimal import Decimal
from unittest.mock import MagicMock
from ibapi.contract import Contract

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import alerts.discord as discord_module
from database import db as db_module
from database import initialize_db
from ib_app import IBApp


@pytest.fixture(autouse=True)
def _no_real_discord_alerts(monkeypatch):
    """Every test runs with no webhook configured, so `alerts.send_alert()` --
    called from risk/gate.py, ib_app.py, run_live.py -- always takes the
    early-return path and never makes a real network/AWS call. Tests that
    want to exercise the actual send path override this within the test
    (see tests/test_alerts_discord.py)."""
    monkeypatch.setattr(discord_module, "_get_webhook_url", lambda: None)


@pytest.fixture
def mock_app():
    """IBApp instance with mocked EClient so no real connection is needed."""
    app = IBApp()
    app.conn = MagicMock()
    app.nextValidId(1)
    return app


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database layer at a fresh temp file for the duration of a test.

    get_connection() reads database.db._DB_PATH at call time, so redirecting that
    module global reroutes every db submodule (account/trading/market_data) here.
    """
    path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "_DB_PATH", path)
    initialize_db()
    return path


@pytest.fixture
def make_contract():
    def _make(symbol="SPY"):
        c = Contract()
        c.symbol = symbol
        c.secType = "STK"
        c.exchange = "SMART"
        c.currency = "USD"
        return c
    return _make
