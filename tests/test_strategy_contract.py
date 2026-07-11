"""
test_strategy_contract.py

Contract tests that every *registered* bar strategy honors the BaseStrategy
interface. This is what makes the framework plug-and-play safe: a new strategy
that breaks the contract fails here rather than at backtest or (worse) live run.

Covers, for each registered strategy:
- correct on_bar signature (accepts position kwarg)
- suppresses signals during warm-up
- once warmed up, returns None or a valid OrderRequest tagged with symbol/name

Plus the multi-symbol generalization (docs/MULTI_STRATEGY_REFACTOR.md → Phase 1):
- on_bars dispatches a single-symbol strategy's on_bar and wraps the result
- a multi-symbol stub receives a multi-key bars dict and emits per-symbol orders
- self.symbol stays a single-symbol convenience and the symbols=/symbol= factory
  arguments both work
"""

import inspect

import pytest

from strategy import STRATEGY_REGISTRY, build_strategy
from strategy.base_strategy import BaseStrategy
from strategy.signal import OrderRequest, ACTIONS, ORDER_TYPES


def _bar(close, dt="2026-01-01"):
    return {"datetime": dt, "open": close, "high": close, "low": close,
            "close": close, "volume": 1000}


ALL_STRATEGIES = sorted(STRATEGY_REGISTRY)


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_registered_strategy_is_base_strategy(name):
    cls = STRATEGY_REGISTRY[name]
    assert issubclass(cls, BaseStrategy)
    assert cls.name == name


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_on_bar_accepts_position(name):
    sig = inspect.signature(STRATEGY_REGISTRY[name].on_bar)
    assert "position" in sig.parameters


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_warmup_returns_none(name):
    strategy = build_strategy(name, symbol="SPY")
    # A single bar must never be enough to fire a signal.
    assert strategy.on_bar(_bar(100.0), position=0.0) is None


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_signals_are_valid_order_requests(name):
    strategy = build_strategy(name, symbol="SPY")
    # Feed a noisy ramp so warm-up completes and signals are produced.
    prices = [100, 101, 99, 103, 97, 105, 95, 110, 90, 115, 88, 120]
    for i, p in enumerate(prices):
        signal = strategy.on_bar(_bar(float(p), dt=f"2026-01-{i + 1:02d}"), position=10.0)
        if signal is None:
            continue
        assert isinstance(signal, OrderRequest)
        assert signal.action in ACTIONS
        assert signal.order_type in ORDER_TYPES
        assert signal.quantity > 0
        assert signal.symbol == "SPY"
        assert signal.strategy == name


# ---------------------------------------------------------------------------
# Multi-symbol generalization (on_bars / symbols)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_on_bars_dispatches_single_symbol_to_on_bar(name):
    # The base on_bars must route the one symbol's bar/position to on_bar and
    # wrap the result in a list — identical signals, just collected.
    via_bars = build_strategy(name, symbols=["SPY"])
    via_bar = build_strategy(name, symbols=["SPY"])
    prices = [100, 101, 99, 103, 97, 105, 95, 110, 90, 115, 88, 120]
    for i, p in enumerate(prices):
        bar = _bar(float(p), dt=f"2026-01-{i + 1:02d}")
        orders = via_bars.on_bars({"SPY": bar}, {"SPY": 10.0})
        assert isinstance(orders, list)
        direct = via_bar.on_bar(bar, position=10.0)
        assert orders == ([] if direct is None else [direct])
        for o in orders:
            assert o.symbol == "SPY"


def test_symbol_convenience_and_factory_aliases():
    # symbols= is canonical; symbol= is the back-compat alias; self.symbol reads
    # the single configured symbol either way.
    a = build_strategy("buy_and_hold", symbols=["SPY"])
    b = build_strategy("buy_and_hold", symbol="SPY")
    assert a.symbols == b.symbols == ["SPY"]
    assert a.symbol == b.symbol == "SPY"


def test_two_symbol_stub_receives_multi_key_bars():
    # Phase 1 acceptance: a trivial 2-symbol strategy can be constructed and its
    # on_bars receives a 2-key bars dict, emitting one self-identifying order per
    # symbol. self.symbol is ambiguous and must raise.
    class TwoSymbolStub(BaseStrategy):
        name = "two_symbol_stub"

        def on_bars(self, bars, positions):
            return [self.buy(1, symbol=sym) for sym in sorted(bars)]

    stub = TwoSymbolStub()
    stub.symbols = ["AAA", "BBB"]
    assert stub.symbols == ["AAA", "BBB"]

    orders = stub.on_bars(
        {"AAA": _bar(100.0), "BBB": _bar(50.0)}, {"AAA": 0.0, "BBB": 0.0}
    )
    assert [o.symbol for o in orders] == ["AAA", "BBB"]
    assert all(o.action == "BUY" and o.strategy == "two_symbol_stub" for o in orders)

    with pytest.raises(AttributeError):
        _ = stub.symbol  # no single symbol to return for a multi-symbol instance


# ---------------------------------------------------------------------------
# Quoting family generalization (on_estimates -> per-symbol mapping)
# ---------------------------------------------------------------------------


def test_on_estimates_returns_per_symbol_quote_mapping():
    # Concrete quoting strategies (e.g. the ERCOT market maker) live in the
    # private working repo; this stub exercises the BaseQuotingStrategy contract
    # in isolation — dispatch to on_estimate, and suppression via a None return.
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from strategy.base_quoting_strategy import BaseQuotingStrategy

    class StubQuotingStrategy(BaseQuotingStrategy):
        name = "stub_quoting"
        max_position = 10

        def on_estimate(self, estimate, position=0.0, as_of=None):
            if abs(position) >= self.max_position:
                return None
            return {"implied": estimate.implied_settlement}

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    estimate = SimpleNamespace(implied_settlement=40.0, timestamp=ts)

    strat = StubQuotingStrategy()
    strat.symbols = ["HB_HOUSTON"]
    quotes = strat.on_estimates({"HB_HOUSTON": estimate}, {"HB_HOUSTON": 0.0}, as_of=ts)
    assert set(quotes) == {"HB_HOUSTON"}
    assert quotes["HB_HOUSTON"]["implied"] == 40.0

    # Suppressed quote (position at cap) → symbol omitted from the mapping.
    capped = StubQuotingStrategy()
    capped.symbols = ["HB_HOUSTON"]
    assert capped.on_estimates({"HB_HOUSTON": estimate}, {"HB_HOUSTON": 50.0}, as_of=ts) == {}
