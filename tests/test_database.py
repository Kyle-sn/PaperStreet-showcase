"""
test_database.py

Hermetic unit tests for the database layer. No TWS connection and no real orders
— every test runs against a throwaway SQLite file.

This replaces the old db_integration_test.py, a script-style end-to-end test that
ran on import (so pytest executed it just by collecting it), required a live paper
account, and placed a real 1-share order every run. The valuable part of that
script was exercising the DB read/write paths; that is covered here without the
broker dependency or the side effect of trading.
"""

import pandas as pd
import pytest

from database import db as db_module
from database import initialize_db
from database import account as adb
from database import market_data as mdb
from database import trading as tdb


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


def _bar(dt, close=100.0):
    return {"datetime": dt, "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 1000,
            "wap": close, "bar_count": 5}


# ---------------------------------------------------------------------------
# market_data_bars — the datetime normalization fix
# ---------------------------------------------------------------------------

def test_raw_and_iso_dates_dedup_to_one_row(temp_db):
    """The same bar supplied as raw "YYYYMMDD" and ISO must not duplicate."""
    mdb.upsert_bars("SPY", [_bar("20260312")], bar_size="1 day")
    mdb.upsert_bars("SPY", [_bar("2026-03-12")], bar_size="1 day")

    df = mdb.get_bars("SPY")
    assert len(df) == 1
    assert df.index[0] == pd.Timestamp("2026-03-12")


def test_upsert_stores_canonical_iso(temp_db):
    mdb.upsert_bars("SPY", [_bar("20260312")], bar_size="1 day")
    with db_module.get_connection() as conn:
        stored = conn.execute("SELECT bar_datetime FROM market_data_bars").fetchone()[0]
    assert stored == "2026-03-12"


def test_get_bars_parses_mixed_legacy_rows(temp_db):
    """get_bars must not crash when raw and ISO rows coexist (pre-migration state)."""
    # Insert two different bars in two different raw/ISO formats directly, bypassing
    # normalization, to simulate a legacy database.
    with db_module.get_connection() as conn:
        conn.executemany(
            "INSERT INTO market_data_bars (symbol, sec_type, bar_size, bar_datetime,"
            " open, high, low, close, volume, what_to_show)"
            " VALUES (?, 'STK', '1 day', ?, 1, 1, 1, ?, 1, 'TRADES')",
            [("SPY", "20260312", 100.0), ("SPY", "2026-03-13", 101.0)],
        )
    df = mdb.get_bars("SPY")
    assert len(df) == 2
    assert df.index.is_monotonic_increasing
    assert list(df["close"]) == [100.0, 101.0]


def test_intraday_datetime_round_trip(temp_db):
    """Intraday bars keep their time component through the canonical form."""
    mdb.upsert_bars("SPY", [_bar("20260609  08:30:00")], bar_size="5 mins")
    df = mdb.get_bars("SPY", bar_size="5 mins")
    assert len(df) == 1
    assert df.index[0] == pd.Timestamp("2026-06-09 08:30:00")


def test_migrate_bar_datetimes_collapses_duplicates(temp_db):
    """A legacy DB holding the same bar twice heals to a single canonical row."""
    with db_module.get_connection() as conn:
        conn.executemany(
            "INSERT INTO market_data_bars (symbol, sec_type, bar_size, bar_datetime,"
            " open, high, low, close, volume, what_to_show)"
            " VALUES (?, 'STK', '1 day', ?, 1, 1, 1, 1, 1, 'TRADES')",
            [("SPY", "20260312"), ("SPY", "2026-03-12")],
        )
    # The collision is resolved by deleting the duplicate (not an update), so the
    # collapse is asserted via the end state rather than the updated count.
    mdb.migrate_bar_datetimes()

    with db_module.get_connection() as conn:
        rows = conn.execute(
            "SELECT bar_datetime FROM market_data_bars WHERE symbol = 'SPY'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2026-03-12"

    # Idempotent: a second pass changes nothing.
    assert mdb.migrate_bar_datetimes() == 0


def test_migrate_bar_datetimes_rewrites_lone_legacy_row(temp_db):
    """A legacy row with no canonical twin is rewritten in place and counted."""
    with db_module.get_connection() as conn:
        conn.execute(
            "INSERT INTO market_data_bars (symbol, sec_type, bar_size, bar_datetime,"
            " open, high, low, close, volume, what_to_show)"
            " VALUES ('SPY', 'STK', '1 day', '20260311', 1, 1, 1, 1, 1, 'TRADES')"
        )
    assert mdb.migrate_bar_datetimes() == 1
    assert mdb.get_latest_bar_date("SPY") == "2026-03-11"


def test_upsert_bars_overwrites_on_repull(temp_db):
    """A re-pull of the same bar must overwrite stale OHLCV, not silently retain it."""
    mdb.upsert_bars("SPY", [_bar("20260312", close=100.0)], bar_size="1 day")
    mdb.upsert_bars("SPY", [_bar("20260312", close=105.0)], bar_size="1 day")

    df = mdb.get_bars("SPY")
    assert len(df) == 1
    assert df.iloc[0]["close"] == 105.0


def test_delete_bars_window(temp_db):
    mdb.upsert_bars("SPY", [_bar("20260310"), _bar("20260311"),
                            _bar("20260312"), _bar("20260313")], bar_size="1 day")
    deleted = mdb.delete_bars_window("SPY", "2026-03-11", "2026-03-12")
    assert deleted == 2
    df = mdb.get_bars("SPY")
    assert len(df) == 2
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    assert dates == ["2026-03-10", "2026-03-13"]


def test_delete_bars_window_respects_what_to_show(temp_db):
    mdb.upsert_bars("SPY", [_bar("20260312")], bar_size="1 day", what_to_show="TRADES")
    mdb.upsert_bars("SPY", [_bar("20260312")], bar_size="1 day", what_to_show="ADJUSTED_LAST")
    deleted = mdb.delete_bars_window("SPY", "2026-03-12", "2026-03-12",
                                     what_to_show="TRADES")
    assert deleted == 1
    assert mdb.get_bars("SPY", what_to_show="ADJUSTED_LAST") is not None
    assert mdb.get_bars("SPY", what_to_show="TRADES") is None


def test_validate_bars_clean(temp_db):
    bars = [_bar("20260312", close=100.0)]
    mdb.upsert_bars("SPY", bars, bar_size="1 day")
    diffs = mdb.validate_bars("SPY", bars, bar_size="1 day")
    assert diffs == []


def test_validate_bars_detects_discrepancy(temp_db):
    mdb.upsert_bars("SPY", [_bar("20260312", close=100.0)], bar_size="1 day")
    fresh = [_bar("20260312", close=105.0)]
    diffs = mdb.validate_bars("SPY", fresh, bar_size="1 day")
    assert len(diffs) > 0
    close_diffs = [d for d in diffs if d["field"] == "close"]
    assert len(close_diffs) == 1
    assert close_diffs[0]["cached"] == 100.0
    assert close_diffs[0]["fresh"] == 105.0


def test_get_latest_bar_date(temp_db):
    mdb.upsert_bars("SPY", [_bar("20260310"), _bar("20260312")], bar_size="1 day")
    assert mdb.get_latest_bar_date("SPY") == "2026-03-12"


# ---------------------------------------------------------------------------
# orders / executions / signals
# ---------------------------------------------------------------------------

def test_order_save_and_status_update(temp_db):
    order_id = tdb.save_order(symbol="SPY", action="BUY", order_type="MKT",
                              quantity=10, ib_order_id=42, strategy_name="mean_reversion")
    assert order_id > 0
    assert tdb.get_order_db_id(42) == order_id

    tdb.update_order_status_by_ib_id(42, status="Filled", filled_quantity=10,
                                     remaining_quantity=0, avg_fill_price=500.0)
    with db_module.get_connection() as conn:
        row = conn.execute("SELECT status, filled_quantity FROM orders WHERE id = ?",
                           (order_id,)).fetchone()
    assert row["status"] == "Filled"
    assert row["filled_quantity"] == 10


def test_signal_save_and_mark_executed(temp_db):
    order_id = tdb.save_order(symbol="SPY", action="BUY", order_type="MKT", quantity=1)
    sig_id = tdb.save_signal(strategy_name="mean_reversion", symbol="SPY",
                             action="BUY", quantity=1, bar_datetime="2026-03-12",
                             bar_close=500.0)
    assert sig_id > 0
    tdb.mark_signal_executed(sig_id, order_id)
    with db_module.get_connection() as conn:
        row = conn.execute("SELECT executed, order_id FROM strategy_signals WHERE id = ?",
                           (sig_id,)).fetchone()
    assert row["executed"] == 1
    assert row["order_id"] == order_id


def _trade_status(group_id):
    with db_module.get_connection() as conn:
        row = conn.execute(
            "SELECT status, closed_at FROM trades WHERE trade_group_id = ?", (group_id,)
        ).fetchone()
    return row


def test_trade_group_round_trip(temp_db):
    """A single-symbol entry+exit shares one trade_group_id; the trades row goes open->closed."""
    strat = "spy_short_reversal"

    # Entry: short SPY. No open group exists yet, so a new one is minted.
    g_entry = tdb.assign_trade_group(strat, "SPY", "SELL", quantity=10)
    o1 = tdb.save_order(symbol="SPY", action="SELL", order_type="MKT", quantity=10,
                        ib_order_id=1, strategy_name=strat, trade_group_id=g_entry)

    # Exit: buy to cover. The open group is reused, not a new one.
    g_exit = tdb.assign_trade_group(strat, "SPY", "BUY", quantity=10)
    assert g_exit == g_entry
    o2 = tdb.save_order(symbol="SPY", action="BUY", order_type="MKT", quantity=10,
                        ib_order_id=2, strategy_name=strat, trade_group_id=g_exit)

    # Both order rows share the one group id.
    with db_module.get_connection() as conn:
        groups = [r[0] for r in conn.execute(
            "SELECT trade_group_id FROM orders WHERE id IN (?, ?)", (o1, o2)
        ).fetchall()]
    assert groups == [g_entry, g_entry]

    # Submission alone does not close the trade — it stays open until fills reconcile.
    assert _trade_status(g_entry)["status"] == "open"

    # Entry fill: net = -10, still open.
    tdb.save_execution(symbol="SPY", side="SLD", shares=10, price=500.0,
                       executed_at="2026-03-12 09:30:00", ib_exec_id="e1",
                       ib_order_id=1, order_id=o1)
    assert _trade_status(g_entry)["status"] == "open"

    # Exit fill: net returns to 0 -> trade closes.
    tdb.save_execution(symbol="SPY", side="BOT", shares=10, price=495.0,
                       executed_at="2026-03-13 09:30:00", ib_exec_id="e2",
                       ib_order_id=2, order_id=o2)
    closed = _trade_status(g_entry)
    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None


def test_trade_group_new_after_close(temp_db):
    """Once a trade closes, the next order for the same (strategy, symbol) mints a fresh group."""
    strat = "spy_short_reversal"
    g1 = tdb.assign_trade_group(strat, "SPY", "SELL", quantity=5)
    o1 = tdb.save_order(symbol="SPY", action="SELL", order_type="MKT", quantity=5,
                        ib_order_id=10, strategy_name=strat, trade_group_id=g1)
    o2 = tdb.save_order(symbol="SPY", action="BUY", order_type="MKT", quantity=5,
                        ib_order_id=11, strategy_name=strat,
                        trade_group_id=tdb.assign_trade_group(strat, "SPY", "BUY", quantity=5))
    tdb.save_execution(symbol="SPY", side="SLD", shares=5, price=500.0,
                       executed_at="2026-03-12 09:30:00", ib_exec_id="e10",
                       ib_order_id=10, order_id=o1)
    tdb.save_execution(symbol="SPY", side="BOT", shares=5, price=498.0,
                       executed_at="2026-03-13 09:30:00", ib_exec_id="e11",
                       ib_order_id=11, order_id=o2)
    assert _trade_status(g1)["status"] == "closed"

    # New entry after the round trip is a distinct logical trade.
    g2 = tdb.assign_trade_group(strat, "SPY", "SELL", quantity=5)
    assert g2 != g1
    assert _trade_status(g2)["status"] == "open"


def test_partial_exit_keeps_trade_open(temp_db):
    """A partial exit fill leaves net shares non-zero, so the trade stays open."""
    strat = "spy_short_reversal"
    g = tdb.assign_trade_group(strat, "SPY", "SELL", quantity=10)
    o1 = tdb.save_order(symbol="SPY", action="SELL", order_type="MKT", quantity=10,
                        ib_order_id=20, strategy_name=strat, trade_group_id=g)
    o2 = tdb.save_order(symbol="SPY", action="BUY", order_type="MKT", quantity=10,
                        ib_order_id=21, strategy_name=strat,
                        trade_group_id=tdb.assign_trade_group(strat, "SPY", "BUY", quantity=10))
    tdb.save_execution(symbol="SPY", side="SLD", shares=10, price=500.0,
                       executed_at="2026-03-12 09:30:00", ib_exec_id="e20",
                       ib_order_id=20, order_id=o1)
    # Only half the exit fills: net = -5, still open.
    tdb.save_execution(symbol="SPY", side="BOT", shares=5, price=498.0,
                       executed_at="2026-03-13 09:30:00", ib_exec_id="e21",
                       ib_order_id=21, order_id=o2)
    assert _trade_status(g)["status"] == "open"


def test_execution_idempotent_on_exec_id(temp_db):
    tdb.save_execution(symbol="SPY", side="BOT", shares=1, price=500.0,
                       executed_at="2026-03-12 09:30:00", ib_exec_id="exec-1")
    tdb.save_execution(symbol="SPY", side="BOT", shares=1, price=500.0,
                       executed_at="2026-03-12 09:30:00", ib_exec_id="exec-1")
    with db_module.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# account / position snapshots
# ---------------------------------------------------------------------------

def test_account_snapshot_round_trip(temp_db):
    snap_id = adb.save_account_snapshot(account="DU123", cash_balance=10000.0,
                                        unrealized_pnl=250.0)
    assert snap_id > 0
    latest = adb.get_latest_account_snapshot("DU123")
    assert latest["cash_balance"] == 10000.0
    assert latest["unrealized_pnl"] == 250.0


def test_position_snapshot_latest_per_symbol(temp_db):
    adb.save_position_snapshot(account="DU123", symbol="SPY", position=10.0)
    adb.save_position_snapshot(account="DU123", symbol="QQQ", position=5.0)
    latest = adb.get_latest_positions("DU123")
    by_symbol = {p["symbol"]: p["position"] for p in latest}
    assert by_symbol == {"SPY": 10.0, "QQQ": 5.0}
