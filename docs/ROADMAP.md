# Roadmap

A living record of what's built, what's actively in progress, and what's been decided against.
Update this file as decisions are made. The "Decided Against" section is as important as the rest.

---

## Working

Things that are functional and reasonably reliable.

- **IBApp core** — EWrapper/EClient combined class, connection to IB Gateway, reader thread
- **Account state** — `updateAccountValue` → `self.account` dict with cash, margins, PnL fields
- **Position state** — `updatePortfolio` → `self.positions` dict keyed by symbol; closed positions removed
- **Order status tracking** — `orderStatus` callback → DB update via `update_order_status_by_ib_id()`
- **Execution persistence** — `execDetails` + `commissionAndFeesReport` → DB records with commission and realized PnL
- **Historical data fetch** — `reqHistoricalData` with threading.Event blocking pattern
- **Account/position snapshots** — written to DB on `accountDownloadEnd` and `updatePortfolio`
- **Thread-safe order ID management** — `get_next_order_id()` with lock
- **Logging** — centralized via `utils/log_config.py`; `setup_logger(__name__)` pattern
- **Strategy framework** — `BaseStrategy` / `BaseQuotingStrategy` ABCs, typed `OrderRequest`
  signal (`strategy/signal.py`), `RollingWindow` indicator state (`strategy/indicators.py`),
  name-based registry + `build_strategy` factory, lifecycle hooks. Runners select strategies by
  config. Contract test in `tests/test_strategy_contract.py`. The interface is **multi-symbol**:
  `on_bars(bars, positions) -> list[OrderRequest]` / `on_estimates(...) -> dict[str, quote]` over
  `self.symbols`, with `on_bar`/`on_estimate` as single-symbol (N=1) convenience methods the base
  dispatches to. (see `STRATEGY.md`, `MULTI_STRATEGY_REFACTOR.md` Phase 1)
- **Backtesting harness (bar family)** — config-driven `run_backtest(BacktestConfig)`
  (`backtesting/`): cache-first `BarDataSource`, `SimBroker` with next-bar-open fills + limit
  crossing + commission/slippage, long/short `Portfolio`, `compute_metrics` (Sharpe, drawdown,
  win rate, …), and a `BacktestResult` with `.summary()`. Hermetic tests in
  `tests/test_backtest.py`. (see `BACKTESTING.md`)
- **Configurable `what_to_show` data basis** — `whatToShow` is threaded end-to-end (IBKR
  request + cache upsert → `MarketDataService` → `BacktestConfig.what_to_show` → loader →
  `run_live` fetch). Default stays `TRADES`; set `ADJUSTED_LAST` for total-return equity
  strategies so research = backtest = live share one basis. The cache keys on it, so TRADES
  and ADJUSTED_LAST series never collide. (see `IBKR_NOTES.md`, `tests/test_market_data_what_to_show.py`)
- **Bar cache data integrity** — `upsert_bars` uses `ON CONFLICT DO UPDATE` (not `INSERT OR
  IGNORE`), so a re-pull of any window **overwrites** stale/corrupt cached bars rather than
  silently retaining them. `delete_bars_window()` provides targeted force-refresh of a date
  range; `validate_bars()` compares cached vs. fresh data and reports per-field discrepancies.
  Validation script: `python -m database.validate_cache SYMBOL WHAT_TO_SHOW [--fix]`.
  (see `DATA_MODEL.md` → Write-path semantics)
- **Benchmark strategies** — `buy_and_hold` and `timing_sma` (long while close > SMA(n))
  registered in `strategy/benchmarks.py`, run through the same engine/cost model as a candidate
  for apples-to-apples comparison. (see `BACKTESTING.md` → benchmarking)
- **IB Gateway + IBC** — headless Gateway (paper, port `4002`) replaces TWS; IBC automates login
  and dismisses dialogs. `AutoRestartTime` survives IBKR's daily forced logoff unattended, no
  re-auth. Not used day-to-day, though — current configuration is market-hours-only operation,
  Mon-Fri 7:30am-3:30pm America/Chicago, enforced by `paperstreet-trading-{start,stop}.timer`
  (`systemctl start`/`stop`, not IBC's own `AutoLogoffTime` — see `DEPLOYMENT.md` §3.2 for why that
  alone didn't actually stop anything once `Restart=always` was added), so the daily reset is
  sidestepped rather than survived. See `DEPLOYMENT.md` §3.2/§5.1/§9.
- **AWS foundation** — EC2 host up in `us-east-1` (`t3.medium`, Ubuntu 24.04) with an Elastic IP,
  SSH-only security group, `ufw`+`fail2ban`, verified patch-without-reboot policy, and an IAM admin
  user (short-lived `aws login` credentials) replacing day-to-day root use. Repo cloned
  via a read-only GitHub deploy key, `.venv` + `ibapi` installed, hermetic tests passing on the box.
- **Gateway + IBC on the VM** — headless Gateway 10.50 + IBC 3.24.2 running under Xvfb on the EC2
  host, authenticating unattended against the paper account (`DUxxxxxxx`), API up on
  `localhost:4002` with a real `ibapi` client connecting end-to-end. Credentials sourced from AWS
  SSM Parameter Store (SecureString) via an instance IAM role (`paperstreet-vm-ssm-read`) at process
  start, not persisted to disk — closes the secrets-manager backlog item below for the VM. Known
  accepted gap: IBC 3.24.2 passes the password as a literal JVM argv, visible via `ps`/procfs on the
  box.
- **Live-loop DB initialization + order-rejection status tracking** — `run_live.py` never called
  `database.initialize_db()` (every research entrypoint does; the live loop didn't), so a fresh
  clone with no pre-existing `paperstreet.db` silently dropped every account/position-snapshot and
  bar-cache write instead of failing loudly — fixed by calling it at `main()` startup (idempotent).
  Separately, `ib_app.py`'s `error()` callback now updates the order's DB status to `'REJECTED'`
  when IBKR rejects an order at submission-time validation (e.g. error 321, Read-Only API) — those
  never reach `orderStatus`, so the row previously stayed `'PENDING'` forever, violating "broker is
  source of truth" (`DEPLOYMENT.md` §6.1). Guarded by checking a DB row actually exists for that
  `ib_order_id` before writing, since `error()`'s `req_id` isn't always an order id. Tests in
  `tests/test_ib_app.py`; the `temp_db` fixture moved from `tests/test_database.py` to
  `tests/conftest.py` so both files share it.
- **Connection-loss recovery (in-process)** — no `connectionClosed`/1100/1102 handling existed
  anywhere, and nothing ever called `connect()` again after the initial one, so `trading_loop`'s
  broad `except Exception` would have retried the same doomed call every 5s forever once the socket
  died (`DEPLOYMENT.md` §6.2). Fixed: `research/session.py::Session.reconnect()` tears down and
  re-establishes the connection from scratch (fresh `IBApp`, fresh `nextValidId` handshake, fresh
  account-update subscription); `run_live.py::reconnect_with_backoff()` checks both the session and
  the orders connection at the top of every loop iteration, suppresses signal generation while
  either is down, and retries with increasing backoff (`RECONNECT_BACKOFF_SECONDS`). Deliberately
  doesn't reset `last_signal` or wait for a fresh account snapshot before resuming — the existing
  RiskGate `ConnectionLivenessRule` already rejects orders on a stale/absent heartbeat, which a
  freshly-reconnected `IBApp` has until real callbacks repopulate it, so no separate "wait for sync"
  step was needed. Tests in `tests/test_run_live.py` (fakes for session/order_app, no real
  connection). Scoped to a socket drop within a still-running process; a full process restart with
  an order in flight is the separate "Order-state reconciliation on restart" item below.
- **Order-state reconciliation on restart** — two fixes, both keyed off `ib_perm_id` (globally
  unique) rather than
  `ib_order_id` (resets to 1 on every Gateway restart): (1) `get_order_db_id()` and
  `update_order_status_by_ib_id()` in `database/trading.py` now check `ib_perm_id` first, falling
  back to the *newest* row sharing an `ib_order_id` rather than blindly matching every row with
  that id — closes the "one fill corrupted 6 unrelated historical rows" incident. (2)
  `orders/order_handler.py::reconcile_open_orders()` calls `reqOpenOrders()` at engine startup
  (`run_live.py::main()`, before the trading loop starts) and seeds `last_signal` from any working
  order found for the traded symbol, so a restart while an order is still `PreSubmitted`/`Submitted`
  no longer resubmits it — position-only dedup (`get_position() == 0`) can't see an unfilled order.
  `ib_app.py::openOrder()` also self-heals the DB row's status/`ib_perm_id` against IBKR's (via the
  new `update_order_status_only()`, which deliberately never touches fill fields it has no data
  for). Tests in `tests/test_database.py`, `tests/test_ib_app.py`, `tests/test_order_handler.py`.
- **Pre-trade RiskGate** — system-wide hard limits enforced before every `app.placeOrder`. The
  `risk/` module (`RiskGate` + isolated, unit-tested rules) is invoked by
  `orders/order_handler.py::place_order`: per-order share/notional cap, sticky kill switch,
  connection-liveness/stale-data guard (via an `IBApp` heartbeat), and a **latching** daily loss
  limit (session realized+unrealized PnL vs. a configured threshold; sticky once tripped, manual
  reset). Rejected orders are neither submitted nor persisted (`place_order` returns `-1`). Limits
  in `RiskConfig` are sized to the $50k/$25k track. Hermetic tests in `tests/test_risk_gate.py`.
  (see `RISK.md`) Aggregate/per-symbol exposure checks remain scoped for
  `MULTI_STRATEGY_REFACTOR.md` Phase 4.
- **Daily position reconciliation** — `DEPLOYMENT.md` §6.3's scheduled check.
  `run_live.py::trading_loop()` calls `run_daily_reconciliation()` once per calendar day, comparing
  IBKR's broker-reported position against the net signed sum of our own recorded `executions`
  (`risk/reconciliation.py::reconcile_position()`, `database/trading.py::get_recorded_net_position()`)
  — an independent check against our own fill history, not a re-read of the same broker data. Any
  divergence trips the `RiskGate` kill switch. Position-only: cash reconciliation needs a local cash
  ledger that doesn't exist yet (see Backlog). Tests in `tests/test_reconciliation.py`. (see `RISK.md`)
- **VM+Pi paper-trading deployment** — a supervised, observed, unattended paper-trading host. VM
  (`paperstreet-ats`, EC2) runs Gateway+IBC and the engine under systemd (`Restart=always`); the Pi
  (`paperstreet-pi`, home network) is the off-box log/heartbeat destination, reachable over
  Tailscale with no public inbound anywhere. Logs ship VM→Pi via native `systemd-journal-upload`/
  `-remote`; the engine emits an explicit heartbeat line that rides the same pipe; a dead-man's
  switch on the Pi and four VM-side choke points (kill-switch trip, hard order rejection, stuck
  reconnect, unhandled exception) alert to Discord (`alerts/discord.py`). Nightly DB backups to S3
  + daily EBS snapshots. See `DEPLOYMENT.md` (architecture) and `OPERATIONS.md` (asset register);
  the build-time incidents and what each one changed are in `DEPLOYMENT_INCIDENTS.md`.
  **Going live is explicitly out of scope** (see Decided Against) — this is paper-only
  infrastructure.

---

## In Progress

Things actively being built or recently started.

*(nothing currently active — see Backlog for what's next)*

---

## Backlog

Prioritized things not yet started.

- **Cash reconciliation** — daily *position* reconciliation is done (see Working). Cash needs a
  local ledger (starting balance + every fill's proceeds − commissions) independent of IBKR's own
  reported `cash_balance` to compare against; no such ledger exists today (see `RISK.md` → Known
  Gaps).
- **`positions/` module** — position query helpers wrapping `ib_app.get_position()`
- **Live market data subscription wiring** — `market_data/` currently only supports historical
  bar fetch (`reqHistoricalData`); no `reqMktData`/streaming subscription exists. Needed for any
  strategy or monitoring path that requires real-time ticks rather than bar-close polling.
- **Aggregate / max-exposure risk check** — the per-order caps, kill switch, stale-data guard, and
  latching daily loss limit are **done** (see Working → Pre-trade RiskGate). What remains is a
  buying-power / max-portfolio-exposure and per-symbol aggregate-exposure check against
  `self.account` and summed positions — scoped for `MULTI_STRATEGY_REFACTOR.md` Phase 4 on top of
  the planned `positions/` helper.
- **Backtesting: quoting family** — event-replay backtester for `BaseQuotingStrategy`
  (settlement/estimate stream, not OHLCV bars); the bar-family harness is built (see Working)
- **Multi-symbol / portfolio backtester** — a portfolio-level extension (or sibling) of the
  bar-family engine that holds a *basket* of instruments under one cash/risk account:
  portfolio sizing across instruments (inverse-vol weighting, vol-targeting), point-in-time
  vol estimation, and cross-instrument accounting. **Hard prerequisite for any cross-sectional
  / diversified strategy** — the current engine is single-symbol / one-instance (`STRATEGY.md`),
  so a basket sized as a single portfolio cannot be expressed or validated. The diversified-trend
  research that was validating this need in vectorized notebooks was **killed at Step 3** (see
  Decided Against / Parked) before reaching the point of exercising this engine, so this item is
  unblocked by no active research and stays a backlog build for whenever the next
  cross-sectional/basket candidate needs it. Stays the custom engine (extended), consistent with
  "Decided Against: third-party / vectorized backtesters."
- **Secrets manager (Windows box only)** — the Windows-side Gateway+IBC (Phase 1–2, see Working)
  still keeps the paper password in plaintext in a local IBC `config.ini` outside the repo,
  acceptable since it's local to a single-user box and out of the repo. The VM side is done (see
  Working → Gateway + IBC on the VM): AWS SSM Parameter Store SecureString + instance IAM role, per
  `DEPLOYMENT.md` §3.3. Not worth porting back to the Windows box — it's being superseded by the
  VM, not kept long-term.
- **CI/CD + deploy pipeline** — no automated path from commit to running host exists today; code
  reaches the box by hand. Scope for the AWS VM build: CI that runs `pytest` (excluding the
  TWS-dependent `market_data/test_market_data.py`, which is kept in the private repo only) on every push/PR; a repeatable deploy step
  (pinned commit/tag → host, restart the service, confirm the heartbeat) with a one-command
  rollback to the previous tag; and AWS resources defined as IaC (CDK or CloudFormation) instead
  of hand-clicked in the console. Deploys should be gated to outside market hours, consistent with
  the market-hours-only decision (`DEPLOYMENT.md` §9). Tool choice (GitHub Actions vs.
  CodePipeline) is open, though a first, narrow slice now exists on GitHub Actions: the
  `systemd-verify` workflow lints every `deploy/*.service`/`*.timer` unit file with
  `systemd-analyze verify` on push/PR (see `DEPLOYMENT.md` §5.3). Still no `pytest` CI, no
  automated deploy step, and no IaC — this only catches a broken unit file before it reaches a
  host.
- **AWS tagging policy** — define before creating AWS resources, not after, so nothing ends up
  untagged. Minimum tag set to decide: `Project=PaperStreet`, `Environment` (`paper` / `live`),
  `Owner`, `ManagedBy` (`cdk` / `manual`), and optionally `CostCenter`/`Component`. Apply it
  through IaC stack-level tags so it happens by default. Activate the tags as cost allocation tags
  in Billing so spend can be broken down by project/environment. Optionally enforce with an AWS
  Organizations tag policy or an AWS Config `required-tags` rule. Separating `paper` and `live` by
  tag (and ideally by account) also sets up IAM scoping later.
- **Strategy warm-up** — pull lookback bars from local DB on `on_start()`; suppress signals until minimum bar count met
- **Research parameter sweeps on the custom engine** — thin grid-search wrapper that loops
  `run_backtest()` over a parameter grid (optionally multiprocessed), feeding the research
  workflow. Built on the custom engine rather than a vectorized library so sweeps honor the
  same inventory-aware, path-dependent fills as validation (see Decided Against). Output is
  candidate parameter sets that must still pass a single `run_backtest()` before paper trading.
- **Portfolio-level backtest evaluation** — combine equity curves across independent
  single-symbol backtests to compute portfolio Sharpe, combined drawdown, and cross-strategy
  correlation. Much lighter than the multi-symbol backtester above (post-processing on
  independent results, not a new engine); deferred until a second strategy reaches production.
- **Capital allocation across strategies** — explicit framework for splitting account equity
  across multiple concurrently live strategies (see `STRATEGY.md` → Multi-Strategy Operation).
  Allocation is implicit today; deferred until more than one strategy reaches paper trading.
- **VM DB backup restore test** — the nightly VM→S3 `paperstreet.db` backup (`DEPLOYMENT.md` §6.4)
  has never actually been restored. A backup that's never been restored isn't a backup: pull a
  backup object down from S3, restore it to a scratch path, and confirm it opens and passes
  `PRAGMA integrity_check` with the expected row counts. Not urgent while the paper soak keeps
  producing fresh, restorable backups nightly — low priority until something actually depends on a
  restore working.

---

## Decided Against / Parked

Things considered and explicitly not being pursued, with the reason.

| Item | Decision | Reason |
|---|---|---|
| Java (v1 architecture) | Abandoned | Rewriting in Python for development speed and ecosystem (pandas, numpy, Jupyter) |
| HFT / sub-second strategies | Out of scope | Infrastructure (Python, IBKR API) not suited to it; not the goal |
| Tick-level order book data | Not pursuing | Overkill for mid-frequency; IBKR's Level 2 data is expensive and adds complexity |
| `spy_short_reversal` candidate (Connors RSI(2)<10 + SMA(200)) | **Parked at §7 OOS (2026-06-14)** | Passed IS (§4) and the §5 plateau check, but **failed the binding OOS gate**: 2015+ Sharpe 0.52 does **not** beat the timing-only baseline (0.63) or buy-and-hold (0.70) on the committed risk-adjusted metric — net of high realized cash rates, both conventions. The reversion entry adds nothing OOS; it's a 200-day timer with extra steps. Predicted crowding decay of a heavily-published system (notes §1). One-shot honored — no re-tune. The *code* and the 200-day timing overlay survive; the *reversion signal* does not. See `research/killed/research_notes/short_reversal_strategy_notes.md` §7; code + readouts in `research/killed/spy_short_reversal/` (`oos_backtest.py` is the one-shot). The `buy_and_hold`/`timing_sma` benchmarks and the `whatToShow`-configurable, rf-aware cost-equal comparison harness built alongside it are general infra that outlive the candidate (see Working). |
| Intraday conditional strategy (QQQ 5-min, conditional on regime) | **Stopped at Step 3 EDA (2026-06-19)** | 224,552 5-min bars loaded from IBKR (2014-01–present), IS = 2015–2021 / OOS = 2022–2025 committed before looking. **Step 3 EDA rejected H1 in-sample**: non-monotonic conditional cross-tab with every bucket \|t\| < 1.3, an overnight-gap-momentum confound, and tail-sign inversion across the 30/60/90-min windows. No backtest run; OOS never opened; no pivot. The Step-2 data infra + `eda.py`'s point-in-time intraday-feature scaffolding are reusable. See `research/killed/intraday_conditional/`, `research/killed/research_notes/intraday_conditional_strategy_notes.md`. |
| Overnight drift (SPY close→open, flat intraday) | **Stopped at Step 3 (2026-06-19)** | Gross overnight Sharpe (0.50) beats B&H (0.41), but **net of turnover costs (1 bp RT × 252 days/yr ≈ 2.5%/yr), Sharpe drops to 0.27 — does not beat B&H**. Failure is structural cost asymmetry, not decay or tail risk. OOS never opened. Data infra (SPY TRADES 8402 bars + IBKR-derived dividend calendar) and the `eda.py` excess-return-decomposition scaffolding are reusable. See `research/killed/overnight_drift/`, `research/killed/research_notes/RESEARCH_WORKFLOW_overnight_drift.md`. |
| Diversified trend on CME futures (8-leg TSMOM, $5M capital) | **Killed at Step 3 (2026-06-21)** | A drought-core confirmation (2016–2019, the regime closest to the expected 2024–2028 forward environment) showed basket net Sharpe of just 0.019 (worst DD −21.57%) vs. 0.481 over the full gate window — the 2011–2015 secular block (Abenomics / EZ-bond-bull) is 98% of base-case P&L, and the basket's edge over the single best leg (6J, 0.429) was only +0.052, within noise. Parameter sweep never started; OOS never opened (quarantined). Trend-as-premium is not refuted — this 8-leg single-lookback daily-resized design at $5M is. Code preserved, not deleted. See `research/killed/research_notes/RESEARCH_WORKFLOW_diversified_trend.md`. |
| Basket stat-arb Stage-0 candidate (clusters A/B/C/D) | **No genuine basket cleared gates (IS only, 2026-06-17)** | Only Cluster A passed the structural gates (ADF, half-life, weight-stability), but its Box-Tiao eigenvector collapsed onto the embedded GOOG/GOOGL dual-class hedge rather than a genuine 4-name edge, with tiny net P&L that turns negative under cost stress. Clusters B/C/D failed on half-life (24–84d, outside the 2–15d target) and/or weight-stability. OOS never opened — moot with no gate-clearer. The Stage-0 primitives (Johansen/Box-Tiao weights, screens, cost-aware sim) remain reusable discovery tooling for future clusters. See `research/killed/basket_statarb/`, `research/killed/research_notes/RESEARCH_WORKFLOW_basket_statarb.md`. |
| Multi-strategy refactor Phases 3–5 (`MULTI_STRATEGY_REFACTOR.md`) | **Parked (2026-07-29)** | N-symbol backtest engine, exposure aggregation, and bar-schema convergence were explicitly gated on the basket-stat-arb Stage 0 research passing. It didn't (see above row) — no active multi-symbol candidate exists to build them for. Phases 1–2 (strategy interface generalization, `trades`/`trade_group_id`) already shipped and stay (see Working → Strategy framework). Not abandoned: the phase scopes and open design decisions in `MULTI_STRATEGY_REFACTOR.md` are the plan for whenever a future basket (or other multi-symbol) candidate clears its own Stage 0. |
| Petroleum status drift (CL/MCL, EIA inventory-surprise event drift) | **Parked at Step 2, G0 data-depth gate FAILS (2026-08-25)** | H1 never tested — this is a data-depth block, not a rejected hypothesis. A CONTFUT max-duration pull nominally spans 2018-01-24 → present (2,116 bars), but 73% of that (everything before 2024-09-17) is degenerate placeholder data (`open==high==low==close`, `volume==0`), most likely a missing NYMEX energy historical-data entitlement on this account — confirmed via 2020-04-20 (the negative-settle day) reading a flat positive $38.83. Real usable CL depth is 2024-09-17 → present, only **101 EIA events**, below the pre-committed 200-event G0 floor. Individual dated FUT contracts don't help either — they only resolve ~12–13 months back on this account. EIA event data (2,290 releases, 1982–2026) and MCL contract specs are fully sourced and reusable. Concrete unblock: a NYMEX-energy data-subscription upgrade (or alternate vendor) to clear real CL depth past ~200 events. See `research/killed/petroleum_status_drift/`, `research/killed/research_notes/RESEARCH_WORKFLOW_petroleum_status_drift.md`. |
| Options strategies | Parked | Infrastructure not built; consider after equities are working end-to-end |
| Multi-account support | Parked | Single personal account for now; `reqAccountUpdates` only supports one subscription at a time anyway |
| External broker / exchange | Not planned | IBKR is the broker; no plans to add Alpaca, Tradier, etc. |
| Real-time dashboard / UI | Not planned | Logging + DB queries are sufficient for monitoring at this stage |
| Third-party / vectorized backtesters (vectorbt, backtrader, zipline, backtesting.py) | Not planned | Every such library imposes its own strategy API, breaking the live/backtest parity that the custom `on_bar`/`OrderRequest` contract exists to guarantee. Vectorized engines (vectorbt) also assume signals are independent of realized inventory — but **all PaperStreet strategies are inventory-aware** (position-gated, e.g. `position < max_position`), which is path-dependent and exactly what those engines model poorly. The custom engine stays the single source of truth. |
| Pre-live gate + live cutover | **Out of scope for now** | The VM+Pi infrastructure (see Working) is paper-only. Whether/when to go live is a trading-side decision (strategy validation, capital allocation) that Kyle owns and iterates on separately. Not abandoned as a future possibility, just not currently in scope. |
