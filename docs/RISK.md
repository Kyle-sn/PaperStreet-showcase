# Risk Controls

Documents the risk controls currently in place, where they live in the code, and known gaps.
Read this before touching `orders/`, `strategy/`, or `run_live.py`.

---

## Current State

PaperStreet is running on a **paper trading account only**. No real money is at risk today.
That said, the risk architecture should be built as if it were live — retrofitting risk controls
after going live is dangerous. The gaps below are known and should be closed before any live
account connection.

---

## Controls In Place

### Strategy-level position gating

Strategies gate entry signals on the injected `position` parameter — they do not track
position internally. This prevents runaway inventory accumulation and keeps strategy
state consistent with broker-confirmed position. Each strategy is responsible for its
own entry/exit gating logic.

Examples in the codebase:
- `spy_short_reversal`: single-entry (`position <= 0` to enter, sell full position to exit)

### Strategy-level order sizing

`order_size` on the strategy controls shares per signal. All order types in `order_types.py`
accept `quantity` as a parameter — there is no automatic sizing at the order layer.

### Duplicate signal suppression

`run_live.py` tracks `last_signal` and suppresses consecutive signals in the same direction.
This prevents re-submitting the same directional order on every bar when the strategy keeps
firing the same action without a fill or position change in between.

### Long-only, DAY orders only

Current strategies only go long (BUY to enter, SELL to flatten). No short positions are taken.
All orders constructed in `order_types.py` use `tif = "DAY"` — no GTC orders that could
persist across sessions and execute unexpectedly.

### Broker-authoritative position

Strategies receive position via `IBApp.get_position(symbol)`, which is populated by the
`updatePortfolio` EWrapper callback — not self-tracked. This means position used for signal
gating always reflects broker-confirmed state, not an internal counter that could drift.

### Pre-trade RiskGate (orders layer)

System-wide, pre-trade hard limits that no strategy can bypass. Implemented in the `risk/`
module (`risk/gate.py::RiskGate`, rules in `risk/rules.py`) and invoked by
`orders/order_handler.py::place_order` — `risk_gate.check(order, contract, account_state)`
runs **before** any order id is consumed, any trade group is assigned, or `app.placeOrder` is
called. A rejected order is neither submitted nor persisted; `place_order` returns the sentinel
`-1` and the gate logs the rejecting rule and reason. Every rule is a self-contained object
unit-tested in isolation against a fabricated `AccountState` (`tests/test_risk_gate.py`); none of
the logic is inlined into `place_order`. The gate runs rules in order and the first rejection
wins: kill switch → connection liveness → daily loss limit → order size.

Rules enforced (parameters in `risk/gate.py::RiskConfig`, sized to the $50k/$25k track):

- **Per-order size cap** — a share cap (always enforced) and a notional cap (enforced when a
  price is resolvable: the order's limit price, else the held position's last market price). A
  market order opening a fresh position has no mark, so the notional cap is skipped-with-warning
  rather than fail-closed; the share cap is the always-on guard.
- **Kill switch** — a sticky flag; when armed, rejects all orders until a manual reset. Applies
  across all running strategies (it gates `place_order`, which every order flows through).
- **Connection-liveness / stale-data guard** — rejects when the connection is not confirmed
  live, no account heartbeat has arrived, or the last heartbeat is older than
  `max_staleness_seconds` (default 420s, set above IBKR's ≤180s update cadence so a quiet-but-live
  connection is not falsely tripped). `IBApp` stamps `_last_heartbeat` on every
  `updateAccountValue`/`updatePortfolio` callback; the snapshot reaches the gate via
  `AccountState.from_session`. This is a **pre-trade check only** — it does not reconnect or
  re-subscribe (see Deferred, below).
- **Daily loss limit (latching)** — a session circuit breaker. Trips when session PnL
  (realized + unrealized, read from `self.account`) drops to or below `daily_loss_limit`
  (default `-$5,000`, the 10%-of-$50k drawdown budget). **The breaker latches**: once tripped it
  stays tripped for the session regardless of any later PnL recovery and requires a manual
  `reset()` — same sticky-flag pattern as the kill switch. A breaker that un-tripped on the next
  heartbeat showing a bounce would not be a breaker.

**Account-state source.** The order handler's own `IBApp` (`ORDERS_CLIENT_ID`) never subscribes
to account/portfolio updates, so PnL/positions/heartbeat come from the live-engine `Session`
(`LIVE_ENGINE_CLIENT_ID`). `run_live.py` builds a fresh `AccountState.from_session(session)` each
bar and passes it into `place_order`. Passing the snapshot in (rather than the gate reading a live
app) is what decouples the gate from any connection and makes the rules testable without TWS.

**Daily-loss-limit granularity (deliberate, documented decision).** PnL is read from IBKR's
account values, which update on position change or **every ≤3 minutes** at most (the cadence
cannot be adjusted). The loss limit is therefore enforced to **±3-minute heartbeat granularity** —
a fast intra-3-minute drawdown can breach the limit before the next heartbeat surfaces it. This is
accepted for now. **Self-marking** (marking live positions × last close between heartbeats for a
tighter, self-computed PnL) is explicitly **parked as a later precision refinement**, not built
here.

### Daily position reconciliation

`DEPLOYMENT.md` §6.3's scheduled check: `run_live.py::trading_loop()` calls
`run_daily_reconciliation()` once per calendar day (not per bar — a state-correctness audit, not a
trading decision), which compares IBKR's broker-reported position (`session.get_position(symbol)`)
against the net signed sum of our own recorded `executions` rows
(`database/trading.py::get_recorded_net_position()`, `risk/reconciliation.py::reconcile_position()`).
This is a genuinely independent check, not a re-read of the same broker data — `get_position()`
already just relays IBKR's own confirmed value (see Broker-authoritative position, above), so the
comparison is against our *own* fill history instead, catching a missed fill, a bust, a corporate
action (split/reverse split/merger), or manual intervention in TWS. Any nonzero divergence trips the
`RiskGate` kill switch (same sticky latch as the daily loss limit) and logs the discrepancy — treated
as a system fault per §6.3, not a warning. **Position-only**: cash reconciliation would need a full
local cash ledger (starting balance + every fill's proceeds − commissions), which doesn't exist yet
(see Known Gaps, below).

## OTHER
- Inverse-vol position sizing must apply a per-instrument vol-estimate floor
  (≈ trailing 1–2yr 10th–20th percentile of realized vol) or an equivalent
  per-instrument leverage cap, so a collapsing vol estimate cannot explode position
  size into a vol spike (the classic vol-targeting blowup).

---

## Known Gaps

These controls do not yet exist. Do not assume they are enforced anywhere.

### No max portfolio exposure check

There is no check against total portfolio value or available cash before placing an order. The
RiskGate caps *per-order* size, not aggregate exposure across orders/positions. The system still
relies on IBKR to reject orders that exceed buying power. Aggregate/per-symbol exposure checks are
scoped for `MULTI_STRATEGY_REFACTOR.md` Phase 4 (built on the planned `positions/` helper).

### No cash reconciliation

Daily reconciliation (above) covers positions only. Reconciling cash would require a local ledger
of starting balance + every fill's proceeds − commissions, independent of IBKR's own reported
`cash_balance` — that ledger doesn't exist. A cash-side bug (e.g. a commission miscount) would not
be caught today.

---

## Deferred (out of scope, tracked elsewhere)

### Reconnect logic — separate resilience build

The connection-liveness guard above is a **pre-trade check**: it *rejects* orders when the
connection/data is stale, but it does **not** detect a dropped connection, re-subscribe to account
updates, or re-warm strategies. That reconnect/resilience work (IBKR error codes 1100/1102, see
`IBKR_NOTES.md`) remains a **separate, deferred build** in the `ROADMAP.md` backlog. The two are
intentionally decoupled: the gate fails safe (no trading on stale data) regardless of whether
reconnect is ever built.

### Self-marking for the loss limit — precision refinement

Parked, as noted above: the daily loss limit reads IBKR-reported PnL at ≤3-minute granularity;
marking positions between heartbeats for tighter PnL is a later refinement, not built here.

---

## Where Risk Checks Belong

Risk is enforced at two layers. This is the authoritative description; `STRATEGY.md` covers the
strategy author's obligations within layer 1 and points here for the rest.

1. **Strategy layer** — strategy-specific rules (max position, entry conditions, flat-at-close).
   Enforced by the strategy not emitting a signal. Already partially in place.

2. **Orders layer (RiskGate, invoked from `orders/order_handler.py`)** — system-wide hard limits
   that no strategy can bypass. Enforced by `RiskGate.check()` (in `risk/`) inside `place_order()`
   before calling `app.placeOrder()`. **Implemented** (see "Pre-trade RiskGate" above). This layer
   rejects:
   - Orders exceeding the per-order share / notional cap
   - Orders when the kill switch is active
   - Orders when the connection is not confirmed live (stale-data guard)
   - All orders once the daily loss limit has latched

Strategies should never call `app.placeOrder()` directly — all orders flow through `place_order()`
so the orders layer can enforce system-wide rules consistently.

---

## Parameters to Set Before Going Live

These are currently configured for paper trading. Review and tighten before connecting a live
account:

| Parameter | Current (paper) | Notes |
|---|---|---|
| `rsi_entry` | — | spy_short_reversal entry threshold |
| `target_notional` | — | spy_short_reversal position size |
| `ACCOUNT_NUMBER` | — | Paper account; must change for live |
| `BROKER_CONNECTION_PORT` | — | Paper IB Gateway port; different port for live Gateway |
| `RiskConfig.max_order_shares` | — | Per-order share cap (fat-finger ceiling); review vs. live sizing |
| `RiskConfig.max_order_notional` | — | Per-order notional cap; sized relative to target_notional |
| `RiskConfig.daily_loss_limit` | — | Latching breaker; sized as a % of the paper track. Tighten for live |
| `RiskConfig.max_staleness_seconds` | — | Stale-data reject threshold; set above IBKR's update cadence |
| `RiskConfig.kill_switch_active` | — | Arm to halt all orders at startup |

_Values redacted in this public mirror — see the private working repo for live parameters._

- **Drawdown budget.** Risk limits are expressed as a percentage of base equity. Define a maximum drawdown threshold (e.g. 10% of the $50k default track = $5k) beyond which the system must halt new order submissions. The pre-live monitor should track current equity against this percentage drawdown budget — **and**, on the $50k default track, against the absolute $25k floor (see Capital Tracks below). At deployable capital the floor is close enough that a percentage budget alone can walk the account into it, so the $25k line is a binding constraint to monitor alongside the percentage, not merely a backstop. (On the large-capital research track the percentage budget dominates and the absolute floor is slack.)
  - **Regime-conditional drawdown (regime/adaptive strategies only).** For a candidate that
    switches behavior by state, size the drawdown budget against the **worst-regime**
    drawdown, not the pooled full-sample drawdown — the deepest drawdown is expected to
    concentrate in the adverse state and a pooled figure averages it away. Where a regime
    gate flattens the strategy, account for the turnover the gating itself adds; an off-state
    is exposure-flat but not cost-free at the transitions. This is a candidate decision —
    `RISK.md` was previously silent on it.
- Account-level limits (kill switch, daily loss limit, total margin usage, drawdown-budget monitor) apply across the sum of all running strategies, not per-strategy. When the kill switch is implemented, it must halt all strategy instances, not just one.
- Per-strategy risk controls (position cap, order sizing) remain the strategy's responsibility but are not a substitute for account-level limits.

## Capital Tracks

PaperStreet researches against two distinct capital parameters. Every
RESEARCH_WORKFLOW_<name>.md must declare which track it's on in Step 0 —
conflating them corrupts sizing assumptions.

- **Default / deployable track — $50,000.** `BacktestConfig.starting_cash`
  default. Applies to every strategy candidate unless explicitly overridden.
  Hard floor: $25,000. The $25k figure is borrowed from the equities PDT
  day-trade minimum, but what's enforced here is the *discipline* — risk
  limits sized so the worst plausible drawdown leaves equity comfortably
  above the floor — not the regulation itself. PDT mechanically only binds
  margin accounts trading equities/options; it does not apply to futures.
  At $50k the floor is the binding constraint, not a backstop — check
  drawdown both as % of equity and against the absolute $25k line.
- **Large-capital research track — parameterized per strategy.** Reserved
  for candidates structurally invalid to evaluate at $50k (e.g. a
  diversified futures basket where one contract is a large fraction of the
  account) or deliberately researched ahead of firm capital growth. The
  figure (e.g. $5M) is set and justified in that strategy's own
  RESEARCH_WORKFLOW_<name>.md. It does not override the global
  `BacktestConfig.starting_cash` default and does not get promoted into
  this file. A candidate that passes research on this track still needs a
  live-deployment capital plan reconciled against actual firm capital
  before going live — passing at $5M ≠ authorization to size at $5M.

---

## Tail-Control Parameter Selection: Drawdown Budget, Not Sharpe

Tail-control parameters — stop distance, position caps, exposure limits, vol targets — are
selected against a **drawdown budget**, not against Sharpe. This generalizes the vol-target
note already made in `RESEARCH_WORKFLOW_diversified_trend.md` Step 4 ("vol-target is a
drawdown-budget choice, not a Sharpe choice") to every tail-control parameter, not only vol
target.

**Mechanism.** For a mean-reversion signal, tightening a stop typically *reduces* Sharpe,
because it cuts the trades that were about to revert. A Sharpe-maximizing search over stop
distance will tend toward "no stop," which is a correct answer to the wrong question — the
stop exists to bound the tail, not to raise risk-adjusted return.

**Sanctioned form of the selection.** Choose the loosest setting whose worst IS drawdown
stays inside the floor-implied ceiling — anchored to the $50k/$25k PDT-floor framing under
Capital Tracks, above — subject to Sharpe not degrading beyond a pre-committed tolerance
versus the uncontrolled variant. State both numbers (the drawdown ceiling and the Sharpe
tolerance) before running the sweep.

**Fill assumption for intrabar-triggered exits.** Any exit whose trigger is intrabar (a stop
level, not a scheduled close) but is evaluated on daily bars must use a conservative fill —
next-session open, or worst-of — never the trigger level itself, and the fill assumption
should itself be a sweep axis. A result that survives only under the optimistic (trigger-
level) fill is a fill-model artifact, not an edge. See the gap-through-stop limitation
already documented in `RESEARCH_WORKFLOW_basket_statarb.md` ("most likely ways this backtest
will lie to you", item 3).

Cross-reference: `docs/BACKTESTING.md` → "Free parameters: pre-declared selection vs
post-hoc re-tuning" covers signal parameters with no canonical value, selected against
Sharpe/plateau. This section covers risk parameters, which are selected against drawdown
even when pre-declared the same way.
