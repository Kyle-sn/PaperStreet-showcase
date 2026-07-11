import time

from contracts.contract_handler import ContractHandler
from orders import order_types
from orders.order_handler import connect_orders_handler, place_order
from research.session import Session
from risk.account_state import AccountState
from risk.gate import build_risk_gate
from strategy.registry import build_strategy
from utils.connection_constants import ACCOUNT_NUMBER, LIVE_ENGINE_CLIENT_ID
from utils.log_config import setup_logger

logger = setup_logger(__name__)

# Live configuration. Swap strategies by changing name/params/symbol only.
# `name` must match a registered strategy (see strategy.registry).
SYMBOL = "SPY"
STRATEGY_NAME = "spy_short_reversal"
STRATEGY_PARAMS = {}  # uses class defaults (rsi_period=2, rsi_entry=10, sma_exit=5, sma_trend=200)

# IBKR data basis for the live fetch. Must match the basis the strategy was
# researched/backtested on, or live signals diverge around ex-dividend dates.
# spy_short_reversal was validated on ADJUSTED_LAST (total-return). Change if
# the strategy changes. See docs/IBKR_NOTES.md.
WHAT_TO_SHOW = "ADJUSTED_LAST"


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


def trading_loop(session, strategy, order_app, contract, risk_gate, symbol=SYMBOL):
    logger.info("Starting live trading loop...")

    last_signal = None
    while True:
        try:
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
            time.sleep(5)


def main():
    session = Session(account=ACCOUNT_NUMBER, client_id=LIVE_ENGINE_CLIENT_ID)
    order_app = connect_orders_handler()
    strategy, contract = initialize_strategy()
    # System-wide pre-trade risk enforcement (see risk/gate.py, docs/RISK.md).
    # Uses RiskConfig defaults sized to the $50k deployable track.
    risk_gate = build_risk_gate()
    strategy.on_start()

    try:
        trading_loop(session, strategy, order_app, contract, risk_gate, SYMBOL)
    finally:
        strategy.on_stop()
        session.disconnect()


if __name__ == "__main__":
    main()
