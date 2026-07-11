# PaperStreet

A systematic trading system for US equities and futures, built on the Interactive Brokers TWS API. Medium-frequency (hold times from minutes to weeks) with an event-driven backtesting engine, a staged research pipeline that kills bad hypotheses cheaply, and an emphasis on running live, continuously, with real capital.

I run market operations at a derivatives exchange by day; this is the participant side of that same world, built for the love of the operational architecture. V1 was Java (2022); V2 is a ground-up Python rewrite (2026).

This is a curated public mirror of an actively-traded system. Live configuration, including broker connection details, account parameters, position sizing, and any strategy currently deployed with capital, is kept in a private working repo. What's here is the architecture, the backtesting and research methodology, and a set of documented strategy rejections. The intent is to show how the system is built and how candidates are validated (and killed), not to hand out live trading parameters.

---

## Start here: `research/killed/`

Most of the research this system has produced ended in a documented rejection, not a deployed strategy — that's by design. Each subdirectory under `research/killed/` is a candidate that went through the staged research workflow (see `research/killed/research_notes/`) and was rejected on its own evidence, with the code, diagnostics, and reasoning preserved:

- **`spy_short_reversal`** — Connors RSI(2) mean-reversion overlay on SPY. Passed in-sample and a plateau check, but parked at the out-of-sample gate: it didn't beat a plain 200-day timing benchmark or buy-and-hold, net of realized cash rates.
- **`overnight_drift`** — close-to-open SPY drift. Gross Sharpe beat buy-and-hold; net of round-trip costs, it didn't. A structural cost-asymmetry failure, not decay.
- **`intraday_conditional`** — conditional intraday continuation/reversion on QQQ. Rejected at the in-sample EDA stage: non-monotonic cross-tabs, no bucket cleared a basic significance bar, and the effect's sign inverted across window lengths.
- **`diversified_trend`** — time-series momentum across a diversified CME futures basket. Killed on a drought-regime robustness check: the strategy's entire lifetime Sharpe was concentrated in one five-year window, and the remaining subperiod was net-negative with a deep drawdown.
- **`basket_statarb`** — cointegrated-basket mean reversion (Johansen/Box-Tiao weights, OU half-life screens). The discovery/validation tooling is built and tested; no candidate basket has cleared the half-life and weight-stability gates yet.

Every candidate declares its kill criteria and capital track *before* looking at out-of-sample data (see `research/killed/research_notes/RESEARCH_WORKFLOW_regime_template.md` for the template this follows), and the one-shot OOS test is honored — no re-tuning after a miss.

---

## Architecture

- **`ib_app.py`** — the core `IBApp` class: a combined `EWrapper`/`EClient` that is the single interface to TWS. Market data, order events, and account state all flow through its callbacks.
- **`backtesting/`** — a custom event-loop engine (`engine.py`), not a vectorized library. Strategies are inventory-aware and path-dependent (signals depend on realized fills), which vectorized backtesters model poorly. Fills happen at the next bar's open (`SimBroker` in `broker.py`) to stay lookahead-safe; `portfolio.py` handles long/short accounting; `metrics.py` computes Sharpe, drawdown, and win rate.
- **`strategy/`** — the strategy interface (`base_strategy.py::BaseStrategy`), a typed `OrderRequest` signal (`signal.py`), and a name-based registry (`registry.py`) so strategies are selected by config, not import. Strategies emit signals only; they never place orders directly.
- **`risk/`** — a pre-trade `RiskGate` that composes independently unit-tested rules (kill switch, connection-liveness/stale-data guard, a latching daily loss limit, per-order size caps) and runs before any order reaches the broker.
- **`database/`**, **`market_data/`**, **`orders/`**, **`positions/`**, **`contracts/`** — the supporting persistence and IBKR-interaction layers described in `docs/ARCHITECTURE.md` and `docs/DATA_MODEL.md`.

See `docs/` for the full design docs: `ARCHITECTURE.md` (system design, threading model, data flow), `BACKTESTING.md` (fill assumptions, lookahead-bias rules), `DATA_MODEL.md` (schema), `IBKR_NOTES.md` (API quirks and rate limits), `STRATEGY.md` (the strategy interface contract), and `RISK.md` (risk controls and known gaps — pre-live parameter values are redacted in this mirror).

---

## Tests

`tests/` covers the backtesting engine, the strategy contract, the risk gate rules, IBApp callbacks (against a mocked `EClient`, no live TWS needed), and the killed `spy_short_reversal` / `basket_statarb` candidates. Run with `pytest`.
