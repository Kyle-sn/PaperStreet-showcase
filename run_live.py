import datetime
import time

from alerts import send_alert
from contracts.contract_handler import ContractHandler
from database import initialize_db
from orders import order_types
from orders.order_handler import connect_orders_handler, place_order, reconcile_open_orders
from research.session import Session
from risk.account_state import AccountState
from risk.gate import build_risk_gate
from risk.reconciliation import reconcile_position
from strategy.registry import build_strategy
from strategy.signal import OrderRequest
from utils.connection_constants import ACCOUNT_NUMBER, LIVE_ENGINE_CLIENT_ID
from utils.log_config import setup_logger

logger = setup_logger(__name__)

# Live configuration. Swap strategies by changing name/params/symbol only.
# `name` must match a registered strategy (see strategy.registry).
# Runs buy_and_hold, not a research candidate: no strategy has cleared its
# OOS gate (docs/ROADMAP.md Decided Against), and this validates engine/infra
# plumbing (connection recovery, broker-as-truth resync, daily start/stop),
# not alpha. Swap back to a real candidate once one exists.
SYMBOL = "SPY"
STRATEGY_NAME = "buy_and_hold"
STRATEGY_PARAMS = {}  # uses class default (target_notional=50_000.0)

# IBKR data basis for the live fetch. Must match the basis the strategy was
# researched/backtested on, or live signals diverge around ex-dividend dates.
# buy_and_hold enters once from flat, so the basis only affects the entry
# price/share count, not signal timing. TRADES (the repo default) is fine.
WHAT_TO_SHOW = "TRADES"

# Backoff schedule (seconds) for reconnect attempts; holds at the last value.
RECONNECT_BACKOFF_SECONDS = [5, 10, 30, 60]


def initialize_strategy():
    strategy = build_strategy(STRATEGY_NAME, symbols=[SYMBOL], params=STRATEGY_PARAMS)
    contract = ContractHandler.get_contract(SYMBOL)
    return strategy, contract


def get_latest_bar(session, symbol=SYMBOL):
    df = session.market_data.get_daily_bars(symbol, what_to_show=WHAT_TO_SHOW)
    return df.iloc[-1].to_dict()


def generate_signals(strategy, bar, session, symbol=SYMBOL):
    # The strategy interface is multi-symbol (on_bars). This is a single-symbol
    # live loop, so wrap the one symbol's bar/position; the returned list holds
    # at most one order. Multi-symbol live driving is a later phase.
    position = session.get_position(symbol)
    signals = strategy.on_bars({symbol: bar}, {symbol: position}) or []
    logger.info(f"Bar processed|position={position}|signals={signals}")
    return signals


def execute_trade(signal, last_signal, order_app, contract, strategy_name=STRATEGY_NAME,
                  risk_gate=None, account_state=None):
    if signal is None:
        return last_signal

    # Suppress duplicate consecutive signals to avoid re-submitting the same
    # directional order on every bar when the strategy keeps firing the same action.
    if last_signal is not None and signal.action == last_signal.action:
        logger.info(f"Suppressing duplicate {signal.action} signal")
        return last_signal

    order = order_types.order_from_request(signal)
    # Pass the strategy name so the order is grouped into the right logical trade
    # (orders.trade_group_id / trades table).
    db_id = place_order(order_app, contract, order, strategy_name=strategy_name,
                        risk_gate=risk_gate, account_state=account_state)

    # Rejected by the risk gate (-1): the order was neither submitted nor saved.
    # Leave last_signal unchanged so a transient rejection (e.g. a momentarily
    # stale heartbeat) is re-attempted on the next bar rather than suppressed as
    # a duplicate. A latched breaker will simply keep rejecting — harmless.
    if db_id == -1:
        logger.warning(f"{signal.action} signal not placed (risk gate rejected); will re-evaluate next bar")
        return last_signal

    return signal


def run_daily_reconciliation(session, symbol, risk_gate):
    """Compare IBKR's broker-reported position against our own recorded fills
    (DEPLOYMENT.md §6.3) and trip the kill switch on any divergence.

    Position-only (see risk/reconciliation.py); cash reconciliation is a
    separate, unbuilt piece (ROADMAP.md). Reconciliation failure is a system
    fault, not a warning -- it halts trading via the existing sticky kill
    switch rather than merely logging.
    """
    result = reconcile_position(symbol, session.get_position(symbol))
    if not result.ok:
        logger.error(
            f"Reconciliation FAILED|symbol={symbol}|broker_position={result.broker_position}"
            f"|recorded_position={result.recorded_position}|divergence={result.divergence}"
        )
        risk_gate.trip_kill_switch(
            reason=f"position reconciliation divergence for {symbol}: "
                   f"broker={result.broker_position} recorded={result.recorded_position}"
        )
    else:
        logger.info(f"Reconciliation OK|symbol={symbol}|position={result.broker_position}")
    return result


def reconnect_with_backoff(session, order_app, connect_orders_fn=connect_orders_handler,
                           sleep_fn=time.sleep):
    """
    Block until both the market-data/account session and the order
    connection are back up, retrying with increasing backoff. A dropped
    connection (Gateway's daily restart, an API disconnect, an IBKR
    maintenance window) is a routine event to tolerate, not an exception to
    propagate (DEPLOYMENT.md §6.2). Returns the (possibly new) order_app --
    once IBKR tears down ORDERS_CLIENT_ID's socket, the old IBApp reference
    is dead and callers must swap in the one returned here.
    """
    max_backoff_index = len(RECONNECT_BACKOFF_SECONDS) - 1
    attempt = 0
    while True:
        session_ok = session.is_connected
        order_ok = order_app.isConnected()
        if session_ok and order_ok:
            break
        delay = RECONNECT_BACKOFF_SECONDS[min(attempt, max_backoff_index)]
        logger.warning(f"Connection down (session={session_ok}, orders={order_ok}); "
                       f"retrying in {delay}s")
        # Alert once a stuck reconnect actually looks stuck (holding at max
        # backoff), then only every 10th attempt after -- a transient blip
        # that clears within a few retries should never page anyone.
        if attempt >= max_backoff_index and (attempt - max_backoff_index) % 10 == 0:
            send_alert(
                "reconnect_stuck",
                f"Still reconnecting after {attempt + 1} attempts "
                f"(session={session_ok}, orders={order_ok})",
            )
        sleep_fn(delay)
        try:
            if not session_ok:
                session.reconnect()
            if not order_ok:
                order_app = connect_orders_fn()
        except Exception as e:
            logger.error(f"Reconnect attempt failed: {e}")
        attempt += 1
    logger.info("Reconnected.")
    return order_app


def trading_loop(session, strategy, order_app, contract, risk_gate, symbol=SYMBOL, last_signal=None,
                 today_fn=datetime.date.today):
    logger.info("Starting live trading loop...")

    last_reconciliation_date = None
    while True:
        try:
            # External liveness signal (see docs/DEPLOYMENT.md 7.2): the Pi's
            # dead-man's switch watches for this over the shipped journal, not
            # for any particular business-logic log line, so it fires every
            # iteration regardless of what happens next.
            connected = session.is_connected and order_app.isConnected()
            logger.info(f"Heartbeat|status=alive|connected={connected}")

            if not connected:
                # Stop acting on stale signals immediately (§6.2 point 1) --
                # don't even attempt to fetch a bar over a dead connection;
                # reconnect before doing anything else.
                order_app = reconnect_with_backoff(session, order_app)
                continue

            # Once per calendar day (DEPLOYMENT.md §6.3), not once per bar --
            # this is a state-correctness audit, not a per-bar decision.
            today = today_fn()
            if today != last_reconciliation_date:
                run_daily_reconciliation(session, symbol, risk_gate)
                last_reconciliation_date = today

            bar = get_latest_bar(session, symbol)
            # Fresh account snapshot each bar so the gate sees current PnL,
            # positions, and heartbeat. Sourced from the live-engine session
            # (the order handler's connection has no account subscription).
            account_state = AccountState.from_session(session)
            for signal in generate_signals(strategy, bar, session, symbol):
                last_signal = execute_trade(signal, last_signal, order_app, contract, STRATEGY_NAME,
                                            risk_gate=risk_gate, account_state=account_state)
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error in trading loop: {e}")
            send_alert("trading_loop_exception", str(e))
            time.sleep(5)


def main():
    # Every callback that persists to SQLite (account/position snapshots, bar
    # cache) assumes the schema already exists; research scripts call this
    # themselves before connecting, but the live entrypoint never did, so a
    # fresh clone with no pre-existing DB file silently dropped every write
    # (caught, logged, not fatal) instead of failing loudly. Idempotent
    # (CREATE TABLE IF NOT EXISTS), so safe on every startup.
    initialize_db()
    session = Session(account=ACCOUNT_NUMBER, client_id=LIVE_ENGINE_CLIENT_ID)
    order_app = connect_orders_handler()
    strategy, contract = initialize_strategy()
    # System-wide pre-trade risk enforcement (see risk/gate.py, docs/RISK.md).
    # Uses RiskConfig defaults sized to the $50k deployable track.
    risk_gate = build_risk_gate()
    strategy.on_start()

    # Broker is source of truth (DEPLOYMENT.md §6.1): a process restart can't
    # see its own in-flight orders in memory, only IBKR knows about them. A
    # working (not yet filled) order for this symbol is seeded as the last
    # signal so execute_trade()'s existing duplicate-suppression skips
    # resubmitting it on the very first bar, instead of only noticing a fill
    # after the fact via get_position() (ROADMAP.md "Order-state
    # reconciliation on restart").
    last_signal = None
    working_order = reconcile_open_orders(order_app, SYMBOL)
    if working_order is not None:
        last_signal = OrderRequest(
            action=working_order["action"],
            quantity=working_order["quantity"] or 1,
            symbol=SYMBOL,
        )
        logger.warning(
            f"Startup reconciliation: found working {working_order['action']} order for "
            f"{SYMBOL} (status={working_order['status']}); suppressing duplicate resubmission"
        )

    try:
        trading_loop(session, strategy, order_app, contract, risk_gate, SYMBOL, last_signal)
    finally:
        strategy.on_stop()
        session.disconnect()


if __name__ == "__main__":
    main()
