# Multi-Strategy Refactor

Source-of-truth doc for the initiative to generalize PaperStreet from a **single-symbol**
strategy system into one that treats **single-name and multi-leg (basket) strategies as the
same shape**, with single-symbol as the degenerate N=1 case.

This doc is authoritative for *why* this refactor is happening and *what order* it proceeds in.
It is intended to survive multiple Claude Code sessions and context resets — read it in full
before touching any phase.

> Status: **Phases 1–2 done; Phases 3–5 parked (2026-07-29).** Phase 1 (strategy interface
> generalization) and Phase 2 (`trades` table + `trade_group_id`) have landed — see their status
> below. Phases 3–5 are gated on the basket-stat-arb Stage 0 research passing (see the sequencing
> note below); Stage 0 ran and **failed** (no genuine basket cleared the structural gates — see
> `ROADMAP.md` → Decided Against / Parked), so there is currently no candidate to build Phases 3–5
> for. Parked, not abandoned: the design decisions and phase scopes below remain the plan for
> whenever a future basket (or other multi-symbol) candidate clears Stage 0. Re-open this doc
> before starting that work rather than re-deriving it. `ROADMAP.md` points here. Move phases
> between Proposed / In Progress / Done / Parked as they change.

---

## Motivation

### The near-term goal

PaperStreet should not be a one-trick pony. The current design (`STRATEGY.md` →
"One instance, one symbol") hard-codes single-symbol assumptions into the strategy interface,
the backtest engine, and the order/trade accounting. That constraint is correct for the
strategies shipped so far, but it blocks an entire class of strategies the system should be
able to host over time — anything that trades a *combination* of instruments as one logical
position.

The design principle for this refactor: **generalize, don't bifurcate.** Multi-leg becomes a
first-class case and single-symbol becomes N=1 of that same case — not a second parallel
strategy family, not a second backtest engine, not a second live loop. Every existing
single-symbol strategy must continue to work with mechanical-only changes.

### The motivating downstream use case (basket stat-arb)

The concrete strategy driving this — supplied by a collaborating researcher — is a
**cointegrated-baskets mean-reversion** system. Short summary of what it is and, more
importantly, what it *demands of PaperStreet*:

- **What it is.** Cluster a universe of hundreds of liquid US equities (on market-factor-
  residualized return correlation), then within each cluster search for a weighted combination
  of 3–8 names whose **spread is mean-reverting** on a multi-day horizon. Enter when the spread
  dislocates (|z| ≥ threshold), exit on reversion or stop. Hold fixed share quantities, no
  rebalancing once on. Goal: 20–50 validated baskets running concurrently as a portfolio, each
  trading episodically (a handful of round trips per year).
- **Why multi-day / why this system.** Classical stat-arb at sub-second is a latency game
  PaperStreet explicitly does not play (`STRATEGY.md` → Frequency and Scope). The edge here is
  at a multi-day horizon where latency doesn't matter — squarely mid-frequency.
- **What it demands of PaperStreet** (the architectural forcing functions):
  1. A strategy instance must consume bars for **N symbols at once** and emit **multiple orders
     atomically** (one leg per name), treated as **one logical trade** for accounting.
  2. **Long/short** legs (a spread is long some names, short others), with **borrow cost** and
     **dividends-on-shorts** in the PnL model.
  3. Account-/portfolio-level exposure awareness across baskets that may **share names**.
  4. A research/optimization stack (clustering, Johansen/Box-Tiao seeding, NES, walk-forward)
     and a fast multi-symbol simulation kernel — **explicitly out of scope for this refactor**
     (see Non-Goals), but the interfaces this refactor lands must not foreclose it.

> Full researcher proposal: **[pointer: `research/killed/research_notes/basket_statarb_proposal.md`]**
> Claude Code does not have access to the conversation where this was first shared — the summary
> above is the load-bearing context; the pointer is for full detail.

### Relationship to existing documented decisions

This refactor deliberately revisits constraints that are currently documented as settled. That
is intentional and is called out here so it does not read as contradicting the docs by accident:

- `STRATEGY.md` → "One instance, one symbol" and "Concurrent strategies must trade disjoint
  symbol universes" — **will be revisited by Phase 4.** The disjointness rule exists because IBKR
  reports one position per symbol and per-strategy attribution was out of scope; baskets that
  share names force this to be confronted (see Phase 3 + Open Decisions).
- `ROADMAP.md` → "Decided Against: third-party / vectorized backtesters" — **not contradicted.**
  That decision rejects *vectorized, inventory-independent* engines. This refactor keeps the
  custom inventory-aware event-loop engine as the single validation gate and merely widens it to
  N symbols. The (future, out-of-scope) numba optimizer kernel is research tooling, not a second
  validation backtester — same tier as `research/` notebooks today.
- `ROADMAP.md` → Backlog "Multi-symbol / portfolio backtester" — this refactor is the
  interface-level groundwork that item assumes but doesn't itself specify.

---

## Design Decisions (already made — do not re-litigate)

These were reasoned through in design discussion and are settled for this initiative. If you
(Claude Code) believe one is wrong, raise it explicitly as a challenge with reasoning — do not
silently work around it.

1. **`on_bar` → `on_bars`.** The bar-family interface becomes
   `on_bars(bars: dict[str, dict], positions: dict[str, float]) -> list[OrderRequest] | None`.
   Single-symbol strategies declare `symbols = [self.symbol]`, receive `{symbol: bar}`, and
   index the one entry. Keep `on_bar` as a convenience the base class dispatches to when
   `len(symbols) == 1`, so existing strategy bodies need minimal change.

2. **`symbol: str` → `symbols: list[str]`** on the strategy instance. `self.symbol` may remain
   as a convenience property returning `symbols[0]` when `len(symbols) == 1` (and raising
   otherwise), to keep single-symbol strategy code readable.

3. **Return type becomes `list[OrderRequest] | None`** for *both* families. A single-symbol
   strategy returning one request is wrapped in a one-element list. This is the highest-leverage
   change — everything downstream (backtest fill loop, live order submission) must handle a list.

4. **Generalize the quoting family too** (decided this session).
   `on_estimate` → `on_estimates(estimates: dict[str, dict], positions: dict[str, float])`, with
   the two-sided-quote return generalized to a per-symbol mapping. Both families move together so
   the system doesn't carry one generalized and one un-generalized interface.

5. **`trades` table + `trade_group_id`** as a first-class grouping concept. One logical trade =
   N order rows sharing a `trade_group_id` (UUID). Single-symbol strategies generate a group id
   per entry and reuse it for the matching exit (gives round-trip accounting for free); baskets
   share one group id across all legs of an entry. Additive schema change — `orders` gains a
   nullable `trade_group_id` first, tightened later.

6. **`BacktestEngine` is the single validation gate for every strategy**, single-name or basket.
   It gets widened to N symbols (calls `on_bars`), stays a readable Python event loop, stays the
   "run once before paper trading" check. The future numba optimizer kernel does **not** replace
   it and is not built here.

7. **Bar-schema convergence.** By the time a bar reaches `on_bars`, it is the same dict shape
   (`DATA_MODEL.md` bar schema) regardless of source (IBKR `reqHistoricalData` today, Databento
   parquet later). Strategies never know the source. No second bar format.

8. **Exposure aggregation is a derived view, not new in-memory state.** Per-symbol aggregate
   exposure across all strategies/trades is computed by summing `self.positions[symbol]`
   (already signed, already broker-authoritative), surfaced via the planned `positions/` helper —
   not by adding a new state store on `IBApp`.

9. **Long/short is a constraint removal, not a feature add.** `self.positions` already stores
   signed positions (`DATA_MODEL.md`: "Negative = short"). `get_position(symbol)` already returns
   the right thing for a short leg. The genuinely new pieces are borrow cost/availability and
   dividends-on-shorts in the cost/PnL model (Phase 5 territory, not this refactor's core).

---

## Phase Sequence

Ordered to minimize rework. Each phase is independently reviewable and (where possible) leaves
the system green before the next begins.

> **Sequencing note (added 2026-06-17).** The order below minimizes *rework* — it is **not** a
> build-now-in-sequence instruction. The downstream consumer driving Phases 3–5 is the basket
> stat-arb candidate, whose research is **staged** (see `RESEARCH_WORKFLOW_basket_statarb.md`).
> Phases 3–5 are **gated on basket-research evidence**: do not build them before that workflow's
> **Stage 0** — a `research/` notebook needing no refactor work — returns a pass. Phase 2 is the
> exception: it is independent of the basket go/no-go and can be built anytime. Each phase below
> now carries its stage-gate.

### Phase 1 — Strategy interface generalization — **DONE**
**Scope:** `on_bar`→`on_bars`, `on_estimate`→`on_estimates`, `symbol`→`symbols`, single
`OrderRequest`→`list[OrderRequest]`, for both families. Backward-compatible dispatch so existing
strategies need mechanical-only edits.
**Files likely in scope:** `strategy/base_strategy.py`, `strategy/base_quoting_strategy.py`,
`strategy/signal.py`, `strategy/registry.py` (`build_strategy` — `symbol=` → `symbols=`),
`backtesting/engine.py` (caller of `on_bar`), `run_live.py` (caller of `on_bar`), every concrete
strategy under `strategy/` (`spy_short_reversal`, benchmarks, ERCOT quoting), and the contract
test `tests/test_strategy_contract.py`.
**Acceptance:** existing strategies pass their tests with mechanical-only changes; a trivial
2-symbol stub strategy can be constructed and receives a 2-key `bars` dict. No behavior change
for single-symbol strategies.

**Status — landed this session.** How it was built (so a later session doesn't re-derive it):
- New `strategy/symbols.py::SymbolsMixin` holds the shared `symbols` list + the `symbol`
  single-symbol convenience property (raises when ambiguous, `""` when unset, back-compat
  setter). Both `BaseStrategy` and `BaseQuotingStrategy` mix it in — "generalize, don't
  bifurcate" applied to the symbol contract itself.
- `on_bars(bars, positions) -> list[OrderRequest] | None` and
  `on_estimates(estimates, positions, as_of) -> dict[str, dict]` are the general entry points;
  `on_bar` / `on_estimate` became overridable convenience methods (default raise
  `NotImplementedError`) that the base dispatches to when `len(symbols) == 1`. Neither is
  `@abstractmethod` now, so a multi-symbol strategy can override only the plural form.
- `buy()` / `sell()` gained an optional `symbol=` to let a multi-symbol strategy tag the right
  leg; single-symbol bodies are unchanged (they fall back to `self.symbol`).
- `build_strategy` / `build_quoting_strategy` take canonical `symbols=` and keep `symbol=` as a
  back-compat keyword alias (notebooks/scripts using `symbol="SPY"` keep working).
- **Single-symbol consumers stay single-symbol.** `backtesting/engine.py` and `run_live.py` now
  call `on_bars({symbol: bar}, {symbol: pos})` and iterate the (≤1-element) order list — they
  *handle a list* (Decision 3) without yet driving N symbols or per-symbol accounting. True
  N-symbol engine driving + per-symbol `Portfolio`/`SimBroker` is **Phase 3**, deliberately not
  started here.
- No concrete strategy body changed: `spy_short_reversal`, benchmarks, and ERCOT still implement
  `on_bar` / `on_estimate` and work through the dispatch. The only call-site churn was
  `build_strategy(..., symbol=)` → `symbols=[...]` in `run_live.py` and `backtesting/runner.py`.
- Tests: full suite green (134 passed); `tests/test_strategy_contract.py` gained on_bars-dispatch,
  a 2-symbol stub (acceptance), the `symbols=`/`symbol=` aliases, and an `on_estimates` mapping
  test. `tests/test_backtest.py` and `tests/test_spy_short_reversal.py` passed **unchanged**.

### Phase 2 — `trades` table + `trade_group_id` — **DONE**
**Stage-gate: none — independent of the basket go/no-go.** Pays rent on the existing single-symbol
book (round-trip accounting) and supports basket Stage 1 (a multi-leg entry = one logical trade).
Safe to build anytime; the only phase not gated on Stage 0.

**Scope:** additive schema; `orders` gains nullable `trade_group_id`; new `trades` table; write
path stamps a group id on entry and reuses on matching exit. Round-trip accounting for existing
single-symbol strategies falls out as a benefit.
**Files likely in scope:** `database/schema.sql`, `database/trading.py`, `orders/order_handler.py`
(stamp group id at submission), `DATA_MODEL.md` (document the new table/column).
**Acceptance:** a single-symbol round trip produces two orders sharing one `trade_group_id` and
one `trades` row transitioning open→closed; hermetic DB test covers it.

**Status — landed this session.** How it was built (so a later session doesn't re-derive it):
- **`trades` table is the open-group registry.** Keyed by `(strategy_name, symbols, status='open')`.
  `database/trading.py::assign_trade_group(strategy, symbol, action, qty)` is the matching
  mechanism: **no open row → entry** (mint a UUID, insert an `open` row recording the entry
  `side`); **open row exists → reuse** its id (the matching exit, or a same-direction scale-in).
  `strategy_name` is matched with SQL `IS` so None-keyed orders group too.
- **The matching is registry-based, not position-based — by necessity.** The order handler runs
  as its own `IBApp` on `ORDERS_CLIENT_ID` and never subscribes to account/portfolio updates, so
  `order_app.get_position()` is always `0.0`. The strategy's position (from the live-engine
  session) never reaches `place_order`. So broker-position-at-submission matching was not an
  option; the `trades` table itself is the source of truth for which group an order joins.
- **Close is fill-driven (not submission-driven).** Decided this session: a trade stays `open`
  from entry submission until executions confirm flat. `save_execution` now calls
  `_reconcile_trade_on_fill`, which sums the group's net signed filled shares (BOT positive, SLD
  negative across all the group's orders) and transitions `open → closed` when they return to 0 —
  inside the execution-insert transaction, and only on a genuinely new fill (`rowcount`-guarded so
  the `INSERT OR IGNORE` idempotency holds). A partial exit leaves net ≠ 0 and stays `open`. The
  "opposite-action exit" is simply what brings net shares back to 0.
- **Status enum `('open','closed','partial','broken')`** — `partial`/`broken` defined now for
  future basket-leg atomicity (Open Decision #4) though nothing emits them yet.
- **Additive + backward-compatible.** `orders.trade_group_id` is nullable; `save_order` gained an
  optional `trade_group_id=`. Fresh DBs get the column from `schema.sql`; established DBs get it
  via `migrate_orders_trade_group()` (idempotent `ALTER TABLE`, run by `initialize_db`, since
  `CREATE TABLE IF NOT EXISTS` won't alter an existing table). `place_order` stamps the group; a
  one-line fix wired `strategy_name` through `run_live.py::execute_trade` (it was calling
  `place_order` without it, which would have keyed every group on `strategy=None`).
- **Out of scope, deliberately:** historical backfill (Open Decision #1: going-forward only) and
  any N-symbol engine/basket work (Phases 3–5). The schema does not foreclose them — `symbols`
  is already plural-capable and the status enum already has basket states.
- Tests: full suite green (137 passed, +3). `tests/test_database.py` gained the open→closed round
  trip (acceptance), a "new group after close" test, and a "partial exit stays open" test. The
  migration ALTER path was verified separately against a pre-Phase-2 `orders` table.

### Phase 3 — `BacktestEngine` N-symbol generalization — **PARKED (2026-07-29)**
**Stage-gate: basket workflow Stage 0 must PASS first — it did not.** The Stage-0 candidate
(clusters A/B/C/D) ran IS-only and no cluster cleared the structural gates (see `ROADMAP.md` →
Decided Against / Parked). OOS was never opened; there is no basket to build this engine for.
Re-open when a future candidate clears Stage 0. This phase is purely basket-enabling — no
single-symbol consumer drives N symbols. It is the gate between Stage 0 (research notebook, no
refactor needed) and Stage 1 (formal validation *through this engine*). Do **not** build it
speculatively before Stage 0 returns a credible OOS result; doing so is the "infra ahead of signal"
trap one level up.

**Scope:** engine calls `on_bars` with a multi-symbol bar dict; `Portfolio` accounts per-symbol
positions under one cash account; `SimBroker` fills a *list* of `OrderRequest`s per bar. For
single-symbol strategies this is invisible (`bars = {symbol: bar}`).
**Files likely in scope:** `backtesting/engine.py`, `backtesting/broker.py`,
`backtesting/portfolio.py`, `backtesting/result.py`/`metrics.py` if per-leg accounting surfaces,
`tests/test_backtest.py`, `BACKTESTING.md`.
**Acceptance:** existing single-symbol backtests reproduce **identical** metrics to pre-refactor
(regression guard); a 2-symbol stub runs end-to-end through the engine.

> **Reuse pointer — shared-cash portfolio replay.** The N-symbol `Portfolio` this phase builds
> (multiple positions accounted under **one cash/risk account**) is the *same primitive* the
> `ROADMAP.md` backlog item **"Portfolio-level backtest evaluation / shared-cash replay"** requires.
> That item (combining multiple strategies under one shared cash account for portfolio Sharpe /
> combined drawdown) should be built **on top of Phase 3's `Portfolio`**, not from scratch — the
> only generalization beyond this phase is letting the shared-cash account be fed by more than one
> strategy's order stream. Do not spin up a parallel portfolio-accounting implementation for it.

### Phase 4 — Exposure aggregation + risk-layer extension — **PARKED (2026-07-29)**
**Stage-gate: basket Stage 0 pass (as Phase 3) — did not pass; see Phase 3.** Folds
in the orders-layer risk controls `RISK.md` flags as pre-live blockers (per-order size cap, kill
switch, exposure check), so it carries standalone value once reached — but it is still not worth
starting before Stage 0.

**Scope:** build the `positions/` helper for per-symbol aggregate exposure across strategies; add
per-symbol aggregate exposure to the (already-backlogged) `orders/order_handler.py` pre-order
checks alongside per-order size cap and kill switch.
**Files likely in scope:** `positions/` (new module per `ROADMAP.md` backlog),
`orders/order_handler.py`, `RISK.md`.
**Acceptance:** aggregate exposure to a symbol held by two strategies sums correctly; a pre-order
check rejects an order that would breach a per-symbol aggregate cap. (Can proceed in parallel
with Phases 2–3; must land before any basket reaches paper trading.)

### Phase 5 — Bar-schema convergence (interface-only) — **PARKED (2026-07-29)**
**Stage-gate: basket Stage 2 (Databento ingestion) — moot with Stage 0 failed.** Buys nothing for Stages 0–1, which run on
IBKR daily bars. Cheap future-proofing for the eventual second data source; defer until Stage 2 is
actually in view.

**Scope:** pin the canonical bar dict as the single shape every data source must produce, so a
future Databento path targets it from day one. **No Databento ingestion built here** — this is
defining/asserting the contract and adding a `source` discriminator where bars are persisted.
**Files likely in scope:** `DATA_MODEL.md`, `database/market_data.py`/`bars` schema (add `source`
column), a schema-conformance test.
**Acceptance:** the bar contract is documented and test-enforced; adding a new source later
requires conforming to it, not changing strategy code.

---

## Non-Goals (explicitly NOT in this refactor)

Do not build these while "in the area." They are the *downstream* of this groundwork, sequenced
separately once the interfaces above are stable.

- The numba multi-symbol **simulation kernel** for the optimizer inner loop.
- **NES / CMA-ES / Johansen / Box-Tiao**, clustering, walk-forward harness, multiple-testing
  discount — the entire `optimize/` + `validate/` research stack.
- **Databento ingestion**, parquet store, corporate-action adjustment pipeline, point-in-time
  universe builder.
- **Borrow-cost / dividends-on-shorts** cost modeling (the *interfaces* must allow it; the model
  itself is later).
- Any **basket strategy implementation** itself.
- Dashboard / portfolio-assembly / basket-retirement logic.

---

## Open Decisions (flag, don't guess)

Things discussed but not pinned down. Surface these when a phase forces the question; do not pick
silently.

1. **`trade_group_id` backfill.** ~~Apply only going forward, or backfill historical
   `orders`/`executions` by matching buys/sells?~~ **RESOLVED (Phase 2): going-forward only.**
   Historical reconstruction is lossy; `trade_group_id` is nullable and only stamped on orders
   submitted after this change. Historical `orders`/`executions` keep `trade_group_id = NULL`.
2. **Quoting-family return shape.** ~~Per-symbol mapping vs. list of quote objects.~~
   **RESOLVED (Phase 1): per-symbol mapping `dict[str, dict]`.** The ERCOT `on_estimate` returns
   an 11-key quote dict with **no symbol field of its own** (`bid`, `offer`, `mid`, `implied`,
   …). A bare `list[dict]` would lose the symbol→quote association unless a `symbol` key were
   injected into that documented quote contract; keying the result externally by symbol mirrors
   the `estimates` input and leaves the quote dict untouched. (Note: Decision 4 already leaned
   "per-symbol mapping"; the ERCOT body confirmed it over the list alternative.)
3. **`self.symbol` convenience property.** **RESOLVED (Phase 1): kept.** It returns `symbols[0]`
   when `len(symbols) == 1`, `""` when unset (the old default), and raises otherwise. `buy()` /
   `sell()` and every shipped single-symbol body read `self.symbol`, and two contract tests
   assert it — keeping it made the concrete-strategy churn **zero**. Multi-symbol strategies use
   `self.symbols` (and `symbol=` on `buy()`/`sell()`); reading `self.symbol` on them raises.
4. **Atomicity of multi-leg submission.** If one leg of a basket entry is rejected by IBKR, what
   is the policy — unwind filled legs, hold and alert, retry? Still **open** (policy not decided),
   but Phase 2 left the room it asked for: the `trades.status` enum is
   `('open','closed','partial','broken')`. Nothing emits `partial`/`broken` yet — the policy that
   decides *when* and *which* is the part still to settle (basket-family work, Phase 3+).
5. **Disjoint-universe rule retirement.** `STRATEGY.md` currently *requires* disjoint symbol
   universes across strategies. Baskets sharing names violate this. Decide whether the rule is
   (a) dropped and replaced by aggregate-exposure accounting (Phase 4), or (b) kept for
   single-symbol strategies and waived only within the basket family. This is a `STRATEGY.md`
   edit that should be made deliberately, not as a side effect.

---

## Doc Maintenance

Per the project's "update docs before committing" rule: as each phase lands, update the docs it touches
(`STRATEGY.md`, `DATA_MODEL.md`, `BACKTESTING.md`, `RISK.md`) and move the phase status here and
in `ROADMAP.md`. This doc is the index; the per-area docs remain authoritative for their area.