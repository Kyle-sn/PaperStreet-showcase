"""
run_backtest.py

CLI entry point for a backtest. Edit the CONFIG block below and run:

    python backtesting/run_backtest.py

Swap the strategy, symbol, data window, or cost model by changing CONFIG only —
no other code changes, exactly like run_live.py. By default it reads bars from
the local cache and only hits TWS on a miss (data_source="auto"); pre-warm a
symbol with `python -m backtesting.data SYMBOL` if you want a fully offline run.
"""

from backtesting.config import BacktestConfig
from backtesting.runner import run_backtest

# Backtest configuration — the single source of truth for this run.
CONFIG = BacktestConfig(
    strategy_name="spy_short_reversal",
    symbol="SPY",
    strategy_params={},  # uses class defaults (rsi_period=2, rsi_entry=10, sma_exit=5, sma_trend=200)
    bar_size="1 day",
    starting_cash=5_000_000,
    commission_per_share=0.005,
    commission_min=1.0,
    slippage_bps=1.0,
    fill="next_open",
    allow_short=False,
    what_to_show="ADJUSTED_LAST",
)


def main():
    result = run_backtest(CONFIG)
    print(result.summary())


if __name__ == "__main__":
    main()
