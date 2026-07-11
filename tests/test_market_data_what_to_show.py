"""
Hermetic tests for the configurable `what_to_show` data basis.

Pins the parity fix: `whatToShow` is threaded from the caller through the IBKR
request *and* the cache upsert, defaulting to "TRADES" so all prior behavior is
unchanged. A TRADES fetch and an ADJUSTED_LAST fetch of the same symbol must be
stored (and read) under distinct cache keys, so they never collide.

No TWS and no real DB: the IB connection is a MagicMock whose reqHistoricalData
populates historical_data the way the EWrapper callback would, and the cache
upsert is monkeypatched to capture the basis it is asked to store under.
"""

from unittest.mock import MagicMock

import market_data.ibkr_client as ibkr_client
from backtesting.config import BacktestConfig
from market_data.ibkr_client import IBKRMarketDataClient
from market_data.market_data_service import MarketDataService


def _make_ib():
    """A mock IB connection that emulates a daily-bar reqHistoricalData reply."""
    ib = MagicMock()
    bar = {"datetime": "20100104", "open": 1.0, "high": 1.0, "low": 1.0,
           "close": 1.0, "volume": 1.0}

    def _populate(*args, **kwargs):
        # The real EWrapper.historicalData callbacks fill this list before
        # historicalDataEnd sets the event; emulate that side effect here.
        ib.historical_data = [bar]

    ib.reqHistoricalData.side_effect = _populate
    ib._historical_data_event.wait.return_value = True
    return ib


def test_what_to_show_threads_to_request_and_cache(monkeypatch):
    captured = {}
    monkeypatch.setattr(ibkr_client._mdb, "upsert_bars",
                        lambda **kw: captured.update(kw) or 1)

    ib = _make_ib()
    client = IBKRMarketDataClient(ib)
    client.get_historical_data("SPY", duration="1 Y", bar_size="1 day",
                               what_to_show="ADJUSTED_LAST")

    # The IBKR request carried the basis through.
    assert ib.reqHistoricalData.call_args.kwargs["whatToShow"] == "ADJUSTED_LAST"
    # And the cache upsert was told to store under that basis (not the default).
    assert captured["what_to_show"] == "ADJUSTED_LAST"


def test_what_to_show_defaults_to_trades(monkeypatch):
    captured = {}
    monkeypatch.setattr(ibkr_client._mdb, "upsert_bars",
                        lambda **kw: captured.update(kw) or 1)

    ib = _make_ib()
    IBKRMarketDataClient(ib).get_historical_data("SPY", duration="1 Y", bar_size="1 day")

    assert ib.reqHistoricalData.call_args.kwargs["whatToShow"] == "TRADES"
    assert captured["what_to_show"] == "TRADES"


def test_service_layer_forwards_basis(monkeypatch):
    captured = {}
    monkeypatch.setattr(ibkr_client._mdb, "upsert_bars",
                        lambda **kw: captured.update(kw) or 1)

    ib = _make_ib()
    service = MarketDataService(ib)
    service.get_daily_bars("SPY", what_to_show="ADJUSTED_LAST")
    assert ib.reqHistoricalData.call_args.kwargs["whatToShow"] == "ADJUSTED_LAST"

    service.get_bars("SPY", duration="1 Y", bar_size="1 day", what_to_show="ADJUSTED_LAST")
    assert ib.reqHistoricalData.call_args.kwargs["whatToShow"] == "ADJUSTED_LAST"


def test_backtest_config_defaults_to_trades():
    # Default preserves prior behavior; opt-in is per-config.
    assert BacktestConfig(strategy_name="x", symbol="SPY").what_to_show == "TRADES"
    assert BacktestConfig(strategy_name="x", symbol="SPY",
                          what_to_show="ADJUSTED_LAST").what_to_show == "ADJUSTED_LAST"
