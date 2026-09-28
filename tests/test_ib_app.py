"""
Pytest version of IBApp callback tests. No TWS connection required — EWrapper
callbacks are invoked directly to verify that state is updated correctly.
"""

import threading
from decimal import Decimal

import pytest
from ibapi.contract import Contract

import ib_app
from database import trading as tdb
from ib_app import IBApp


# ---------------------------------------------------------------------------
# nextValidId / order ID
# ---------------------------------------------------------------------------

def test_next_valid_id_sets_order_id():
    app = IBApp()
    assert app.nextOrderId is None
    app.nextValidId(42)
    assert app.nextOrderId == 42


def test_get_next_order_id_returns_and_increments(mock_app):
    first = mock_app.get_next_order_id()
    second = mock_app.get_next_order_id()
    assert first == 1
    assert second == 2


def test_get_next_order_id_thread_safe(mock_app):
    mock_app.nextOrderId = 1
    ids = []
    lock = threading.Lock()

    def grab():
        oid = mock_app.get_next_order_id()
        with lock:
            ids.append(oid)

    threads = [threading.Thread(target=grab) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 20
    assert len(set(ids)) == 20, f"Duplicate IDs: {sorted(ids)}"


# ---------------------------------------------------------------------------
# updatePortfolio / get_position
# ---------------------------------------------------------------------------

def test_update_portfolio_stores_position(mock_app, make_contract):
    mock_app.updatePortfolio(make_contract("SPY"), Decimal("100"),
                             500.0, 50000.0, 490.0, 1000.0, 200.0, "U123")
    assert mock_app.get_position("SPY") == 100.0


def test_update_portfolio_zero_removes_symbol(mock_app, make_contract):
    mock_app.updatePortfolio(make_contract("SPY"), Decimal("100"),
                             500.0, 50000.0, 490.0, 1000.0, 200.0, "U123")
    mock_app.updatePortfolio(make_contract("SPY"), Decimal("0"),
                             500.0, 0.0, 490.0, 0.0, 0.0, "U123")
    assert mock_app.get_position("SPY") == 0.0
    assert "SPY" not in mock_app.positions


def test_get_position_unknown_symbol_returns_zero(mock_app):
    assert mock_app.get_position("AAPL") == 0.0


def test_update_portfolio_overwrites_with_latest(mock_app, make_contract):
    mock_app.updatePortfolio(make_contract("SPY"), Decimal("50"),
                             500.0, 25000.0, 490.0, 500.0, 100.0, "U123")
    mock_app.updatePortfolio(make_contract("SPY"), Decimal("75"),
                             502.0, 37650.0, 491.0, 825.0, 150.0, "U123")
    assert mock_app.get_position("SPY") == 75.0


def test_update_portfolio_tracks_multiple_symbols(mock_app, make_contract):
    mock_app.updatePortfolio(make_contract("SPY"), Decimal("100"),
                             500.0, 50000.0, 490.0, 1000.0, 200.0, "U123")
    mock_app.updatePortfolio(make_contract("QQQ"), Decimal("50"),
                             400.0, 20000.0, 395.0, 250.0, 50.0, "U123")
    assert mock_app.get_position("SPY") == 100.0
    assert mock_app.get_position("QQQ") == 50.0
    assert mock_app.get_position("IWM") == 0.0


# ---------------------------------------------------------------------------
# updateAccountValue
# ---------------------------------------------------------------------------

def test_update_account_value_cash_balance(mock_app):
    mock_app.updateAccountValue("TotalCashBalance", "100000.50", "USD", "U123")
    assert mock_app.get_current_cash_balance() == 100000.50


def test_update_account_value_maintenance_margin(mock_app):
    mock_app.updateAccountValue("MaintMarginReq", "5000.00", "USD", "U123")
    assert mock_app.get_current_maintenance_margin() == 5000.00


def test_update_account_value_initial_margin(mock_app):
    mock_app.updateAccountValue("InitMarginReq", "3000.00", "USD", "U123")
    assert mock_app.get_current_initial_margin() == 3000.00


def test_update_account_value_realized_pnl(mock_app):
    mock_app.updateAccountValue("RealizedPnL", "750.00", "USD", "U123")
    assert mock_app.get_realized_pnl() == 750.00


def test_update_account_value_unrealized_pnl(mock_app):
    mock_app.updateAccountValue("UnrealizedPnL", "1250.75", "USD", "U123")
    assert mock_app.get_unrealized_pnl() == 1250.75


def test_update_account_value_non_usd_cash_ignored(mock_app):
    mock_app.updateAccountValue("TotalCashBalance", "100000.00", "USD", "U123")
    mock_app.updateAccountValue("TotalCashBalance", "9999.00", "EUR", "U123")
    assert mock_app.get_current_cash_balance() == 100000.00


def test_account_values_start_as_none():
    app = IBApp()
    assert app.get_current_cash_balance() is None
    assert app.get_current_maintenance_margin() is None
    assert app.get_current_initial_margin() is None
    assert app.get_realized_pnl() is None
    assert app.get_unrealized_pnl() is None


# ---------------------------------------------------------------------------
# error callback
# ---------------------------------------------------------------------------

def test_error_info_codes_do_not_raise(mock_app):
    mock_app.error(-1, 2104, "Market data farm connection is OK:usfuture")
    mock_app.error(-1, 2106, "HMDS data farm connection is OK:ushmds")
    mock_app.error(-1, 2158, "Sec-def data farm connection is OK:secdefil")


def test_error_real_error_does_not_raise(mock_app, temp_db):
    # req_id doubles as an IB order id for order-level errors (see below), so
    # this needs a real (temp) DB to hit get_order_db_id() without a table
    # lookup blowing up on a codebase-relative data/paperstreet.db that may
    # not have been initialized yet (exactly the run_live.py gap this guards).
    mock_app.error(1, 200, "No security definition has been found for the request")
    mock_app.error(1, 162, "Historical Market Data Service error message")


def test_error_order_rejection_alerts(mock_app, temp_db, monkeypatch):
    alerts = []
    monkeypatch.setattr(ib_app, "send_alert", lambda kind, msg: alerts.append((kind, msg)))
    tdb.save_order(symbol="SPY", action="BUY", order_type="MKT", quantity=65, ib_order_id=1)

    mock_app.error(1, 321, "Error validating request.-'v' : cause - The API "
                          "interface is currently in Read-Only mode.")

    assert len(alerts) == 1
    kind, message = alerts[0]
    assert kind == "order_rejected"
    assert "321" in message


def test_error_order_rejection_updates_db_status(mock_app, temp_db):
    # IBKR validates and rejects some orders (e.g. 321 Read-Only API) before
    # they ever enter the book, so orderStatus never fires for them and the
    # row would stay 'PENDING' forever without this. See ib_app.py::error().
    order_db_id = tdb.save_order(
        symbol="SPY", action="BUY", order_type="MKT", quantity=65, ib_order_id=1,
    )
    mock_app.error(1, 321, "Error validating request.-'v' : cause - The API "
                          "interface is currently in Read-Only mode.")
    with tdb.get_connection() as conn:
        row = conn.execute(
            "SELECT status, filled_quantity, remaining_quantity, why_held "
            "FROM orders WHERE id = ?", (order_db_id,),
        ).fetchone()
    assert row["status"] == "REJECTED"
    assert row["filled_quantity"] == 0
    assert row["remaining_quantity"] == 0
    assert "Read-Only" in row["why_held"]


def test_error_advisory_code_does_not_reject_order(mock_app, temp_db, monkeypatch):
    # 399 is an advisory ("order queued until market open"), not a rejection --
    # the order still proceeds to a real orderStatus. An earlier cut of this
    # fix treated any non-farm-status error as a rejection and would have
    # stomped a legitimately-proceeding order back to REJECTED.
    alerts = []
    monkeypatch.setattr(ib_app, "send_alert", lambda kind, msg: alerts.append((kind, msg)))
    order_db_id = tdb.save_order(
        symbol="SPY", action="BUY", order_type="MKT", quantity=65, ib_order_id=1,
    )
    mock_app.error(1, 399, "Order Message:\nBUY 65 SPY ARCA\nWarning: Your "
                          "order will not be placed at the exchange until "
                          "2026-09-24 09:30:00 US/Eastern.")
    assert alerts == []  # advisory, not a hard rejection -- no alert
    with tdb.get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM orders WHERE id = ?", (order_db_id,),
        ).fetchone()
    assert row["status"] == "PENDING"


def test_error_unmatched_req_id_does_not_touch_db(mock_app, temp_db):
    # req_id here is a historical-data/market-data request id, not an order
    # id -- no order row exists with that ib_order_id, so this must be a
    # no-op rather than mistakenly rejecting an unrelated (or nonexistent) order.
    mock_app.error(3001, 2188, "Up-to-the-second historical data requires "
                              "additional subscription for the API.")
    assert tdb.get_order_db_id(3001) is None


# ---------------------------------------------------------------------------
# openOrder / openOrderEnd -- startup reconciliation (DEPLOYMENT.md §6.1)
# ---------------------------------------------------------------------------

def _order(action="BUY", quantity=65, perm_id=999):
    from ibapi.order import Order
    o = Order()
    o.action = action
    o.totalQuantity = quantity
    o.permId = perm_id
    return o


def _order_state(status="PreSubmitted"):
    from ibapi.order_state import OrderState
    s = OrderState()
    s.status = status
    return s


def test_open_order_tracks_in_memory(mock_app, make_contract):
    mock_app.openOrder(1, make_contract("SPY"), _order(), _order_state())
    assert mock_app.open_orders[1] == {
        "symbol": "SPY", "action": "BUY", "quantity": 65.0,
        "perm_id": 999, "status": "PreSubmitted",
    }


def test_open_order_self_heals_db_status_and_perm_id(mock_app, temp_db, make_contract):
    order_db_id = tdb.save_order(symbol="SPY", action="BUY", order_type="MKT",
                                 quantity=65, ib_order_id=1)
    mock_app.openOrder(1, make_contract("SPY"), _order(), _order_state("PreSubmitted"))
    with tdb.get_connection() as conn:
        row = conn.execute("SELECT status, ib_perm_id FROM orders WHERE id = ?",
                           (order_db_id,)).fetchone()
    assert row["status"] == "PreSubmitted"
    assert row["ib_perm_id"] == 999


def test_open_order_does_not_zero_out_fill_progress(mock_app, temp_db, make_contract):
    # openOrder() carries no filled/remaining/avg_fill_price -- must not stomp
    # a partially-filled order's recorded progress (see update_order_status_only).
    order_db_id = tdb.save_order(symbol="SPY", action="BUY", order_type="MKT",
                                 quantity=65, ib_order_id=1)
    tdb.update_order_status(order_db_id, status="Filled", filled_quantity=65,
                            remaining_quantity=0, avg_fill_price=764.07)
    mock_app.openOrder(1, make_contract("SPY"), _order(), _order_state("PreSubmitted"))
    with tdb.get_connection() as conn:
        row = conn.execute("SELECT filled_quantity, avg_fill_price FROM orders WHERE id = ?",
                           (order_db_id,)).fetchone()
    assert row["filled_quantity"] == 65
    assert row["avg_fill_price"] == 764.07


def test_open_order_no_matching_db_row_does_not_raise(mock_app, temp_db, make_contract):
    mock_app.openOrder(999, make_contract("SPY"), _order(), _order_state())
    assert mock_app.open_orders[999]["symbol"] == "SPY"


def test_open_order_end_sets_event(mock_app):
    assert not mock_app._open_order_end_event.is_set()
    mock_app.openOrderEnd()
    assert mock_app._open_order_end_event.is_set()
