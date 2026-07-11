# Cointegrated-Baskets Stat-Arb — Research Proposal

> Source: proposal from collaborating researcher (originally shared via Slack). Reformatted to
> markdown; content preserved as written. This is the full reference for the strategy that
> motivates the multi-strategy refactor (see `MULTI_STRATEGY_REFACTOR.md`).

---

## The big picture

Classical stat-arb mean reversion (NQ/MNQ, MSFT/GOOGL pairs) is a latency game now and we can't
win there. Instead, we build baskets of stocks whose weighted combination is mean-reverting, and
trade the dislocations on a multi-day horizon where latency doesn't matter. Universe is hundreds
of liquid US equities. No rebalancing once a basket is on — enter when the spread dislocates, exit
when it reverts (or hits a stop). Goal is to end up with 20–50 validated baskets running
concurrently as a portfolio.

---

## Universe construction and clustering

Don't search for cointegration across the whole universe at once — cointegration between
economically unrelated names is almost always spurious. We cluster first, then search within
clusters. Clustering is on residual return correlation after stripping the market factor (regress
out SPY or PC1, otherwise every cluster is just "high beta"), sanity-checked against GICS
industry. Target cluster size 5–30 names. Clusters re-form at each walk-forward refit,
point-in-time. Universe filtering happens upstream: ADV floor, borrowable, listed at the point in
time being evaluated (need delisted names in the dataset too or the backtest is
survivorship-biased).

---

## The optimization is two phases

This is the core design. Splitting it makes each stage's search space tractable and each stage's
objective well-suited to its dimensionality.

### Phase 1 — Shape (find the cointegrating direction)

NES optimizes the full N-dim weight vector for the cluster on daily spread data. Composite
objective:

```
fitness = −capped_ADF + λ₁ · amplitude_weighted_1σ_excursion_count − λ₂ · half_life_band_penalty
```

The ADF cap prevents the optimizer from chasing already-extreme stationarity into noise. The 1σ
excursion term rewards spreads that travel — prevents the degenerate tight-wiggle solution that's
statistically stationary but has no tradeable amplitude. Amplitude-weighted means each excursion
contributes its max |z| reached, not just a count, so a spread poking just past 1σ scores less
than one traveling to 1.5σ. Half-life penalty keeps the answer in the 2–15 day band — fast enough
to compound, slow enough to avoid the latency game. Constraints: sparsity (3–8 names), max
single-name weight, normalized gross exposure.

NES is seeded from Johansen and Box-Tiao eigenvectors so we're not starting random. Both are
eigenvalue problems on the cluster's price matrix, essentially instant. Johansen's top
cointegrating vector and Box-Tiao's most-predictable direction usually agree closely and both land
in the right neighborhood. We compute both, use them as separate NES initial means (with tight
initial covariance), and let phase 1 refine whichever lands in the better basin. Massive
convergence speedup vs random init, and the seed also serves as a sanity check — NES wandering far
from both seeds is a flag.

One subtle point: Johansen is classically formulated on log prices, but we hold fixed share
quantities (no rebalancing), so the spread we trade is in raw prices. We use Johansen on log
prices for seeding only — it just needs to be in the right neighborhood — and let NES refine in
raw-price space.

### Phase 2 — Tuning (trade the spread)

Weights from phase 1 are mostly frozen. Phase 2 has two sub-phases, both using the minute-bar
event simulation:

**2a — Trading policy.** Optimize `z_enter`, `z_exit`, `z_stop`, time-stop-multiple-of-half-life,
persistence filter (N consecutive bars beyond threshold before entry fires), z-score lookback
window, position sizing scalar, and asymmetric thresholds (separate enter/exit for long-spread vs
short-spread trades — real baskets have directional skew). ~8-dim search space, CMA-ES or NES
handles it easily. Most of these parameters don't change the spread series itself — they only
choose which excursions to trade — so the phase-1 ADF cap stays satisfied by construction. The
exceptions are lookback window and position sizing; for those we recheck.

**2b — Weight refinement with zeroing.** Each symbol gets a multiplier `sᵢ ∈ [0, s_max]` applied
to its phase-1 weight. `s=0` zeros that name out of the basket entirely. Lets NES drop symbols
that phase 1 included but that hurt OOS, and rebalance contribution without redoing the full N-dim
search. ADF must be re-checked here since the spread series changes — phase-1 cap becomes a hard
constraint enforced via penalty. Phase 2a's policy scalars are frozen during this pass.

Phase 2 objective:

```
fitness = PnL_after_costs + λ · round_trip_count − penalties
```

PnL is from the event simulation with proper costs (commissions, half-spread, borrow × days held,
dividends on shorts). Round-trip count is the number of completed entry→exit cycles, winners and
losers both — we want trades to happen and resolve. This term is a real overfitting defense:
PnL-only can find a basket that made everything from one huge in-sample excursion that won't
recur; requiring high trip count forces repeatable edge. Hard floor of minimum ~8–10 round trips
over the in-sample window applied as a constraint, not a soft term — baskets below that don't have
enough events to be statistical evidence of anything.

---

## Trade lifecycle (what gets simulated)

Enter at `|z| ≥ z_enter` (after persistence filter), exit at `|z| ≤ z_exit`, z-stop at `z_stop`,
time-stop at `time_stop_mult × OU_half_life`. Stops live inside the fitness simulation so the
optimizer can't be rewarded for baskets that look great only because in-sample nothing ever broke.
Spread is in shares, not dollars — fixed share quantities held through the trade, since we don't
rebalance. Optimization is on raw prices for the same reason.

---

## Data resolution — two-layer design

This matters and the code structure should reflect it.

**Daily data** (derived from minute, RTH closes) is used for everything describing the multi-day
equilibrium: clustering, Johansen/Box-Tiao seeding, phase 1 weight estimation, ADF, OU half-life,
weight-stability diagnostics. Minute-level variation within a day is mostly microstructure noise
and biases these estimates; daily is also vastly cheaper computationally inside NES.

**Minute data** (RTH only, session-aware) is used for everything describing what we'd actually
experience holding the position: the event-driven trading simulation in phase 2, entries with
persistence filtering, exits with gap-aware stop fills (overnight gaps fill at next session open,
not at the stop level — backtest eats the gap the way live trading will), realized PnL, eventually
the live intraday signal checker.

Same minute-bar store on disk. Daily layer derived from it (last RTH bar per session). One
corporate-action adjustment pipeline applied to both. One shared session calendar. Half-life
measured in trading time not wall-clock, so overnight gaps don't pose as fast reversion.

---

## Data source

Databento OHLCV-1m on the consolidated equities feed, plus security master and corporate actions.
Sequencing to keep the bill reasonable: pull daily for the full universe first (unblocks
clustering, phase 1, walk-forward validation), then pull minute for the survivors' constituents.

Corporate actions matter — both for split adjustments (unadjusted splits look like glorious
dislocations) and for dividends on shorts (real cash flow, must be in the PnL model). Maintain two
price series per symbol: fully adjusted for signals and estimation, split-adjusted-only for
execution-side PnL.

---

## Walk-forward validation

This is where overfitting gets killed and arguably the most important part of the project. Fit on
a 3-year window, trade OOS on the next 6 months, roll forward, repeat over the full history. A
basket family survives only if:

- It makes money OOS across multiple folds
- Weight vectors are stable between adjacent refits (cosine similarity above some threshold)
- OOS half-life roughly matches in-sample
- Realized round-trip count OOS is in the ballpark of in-sample

**Critical:** the full pipeline (phase 1 + 2a + 2b) reruns on every fold. We don't fit phase 1
per-fold and reuse phase 2 parameters from elsewhere — that's a hidden look-ahead bias. Whole
stack, every fold.

Then a multiple-testing discount on top: we'll be screening hundreds of candidate baskets, so the
bar for "real" is well above the naive t-stat. Rank by OOS performance and assume the top tail is
partly luck.

---

## Portfolio assembly

20–50 validated baskets running concurrently. Each basket trades episodically (a handful of round
trips per year), so the strategy works by having something always in play. Dedup baskets that
share heavy weight in the same names (one underlying bet wearing two costumes), correlation check
between basket spread series, per-basket and per-name capital caps. Episodic entries mean capital
sits idle between signals — that's fine and reflected honestly in the return calculation, not
hidden with notional accounting.

Live monitoring: rolling OOS hit rate and realized half-life per basket. Retire a basket when its
behavior drifts out of band rather than averaging down into a structural break (classic stat-arb
death). Refit cadence quarterly.

---

## Stack

Proper Python package, not notebooks. Rough module layout:

- **`data/`** — Databento ingest, parquet store partitioned by symbol, corporate-action adjustment
  pipeline, session calendar, daily-from-minute derivation, point-in-time universe builder
- **`spread/`** — z-score computation, OU fit, ADF, the numba simulation kernel (this is the hot
  loop, gets reused as phase 1 fitness, phase 2 fitness, walk-forward backtester, and eventually
  the live signal checker)
- **`optimize/`** — clustering, Johansen and Box-Tiao seeding, NES, phase 1 / phase 2a / phase 2b
  orchestration
- **`validate/`** — walk-forward harness, weight-stability diagnostics, multiple-testing discount
- **`portfolio/`** — assembly, dedup, capital caps, basket retirement logic

Unit tests throughout. Dash dashboard on top for inspecting clusters, candidate baskets,
walk-forward fold results, and live basket health.

---

## First milestone before any optimization

Data layer end-to-end (ingest → adjust → store → daily derivation → PIT universe) plus the numba
simulation kernel, validated on GOOG/GOOGL as the known-good basket — should score well — against
a few random pairs that should score badly. That proves the machinery before we let NES loose on
it. After that's clean, phase 1 on a single cluster, then phase 2, then walk-forward, then
portfolio assembly, then dashboard.

---

## Build order (top to bottom)

Steps 1–9 are foundation, 10–18 are the research engine, 19–22 are live trading, 23–25 are the
dashboard, 26–27 are operations.

### Foundation (1–9)

1. **Project scaffolding** — Stand up the Python package skeleton, dependency management, linting,
   CI, and test harness so every subsequent piece lands in a consistent structure.
2. **Databento data ingestion** — Pull OHLCV-1m for the equities universe plus security master and
   corporate actions, with retry/resume logic since the historical pull is large.
3. **Parquet store and adjustment pipeline** — Partition the raw bars to disk, apply split and
   dividend adjustments to produce both fully-adjusted and split-adjusted-only price series.
4. **Session calendar and daily derivation** — Build the shared RTH trading calendar and derive
   the daily price layer from minute bars (last RTH bar per session).
5. **Point-in-time universe builder** — Maintain a date-indexed registry of which symbols were
   listed, liquid (ADV floor), and borrowable, including delisted names, so backtests never see
   survivorship bias.
6. **Spread and statistics primitives** — Implement z-score, OU half-life fit, capped ADF, and the
   spread series construction (raw prices, share-quantity weights) as reusable, tested functions.
7. **Numba simulation kernel** — Write the hot event-driven loop that takes a weight vector +
   policy params + minute bars and emits trades, PnL, and round-trip count, gap-aware on overnight
   stops.
8. **Cost model** — Encode commissions, half-spread, borrow cost × days held, and dividends on
   shorts, callable from the simulation kernel.
9. **Known-good validation harness** — Run the data layer + kernel end-to-end on GOOG/GOOGL
   (should score well) versus random pairs (should score badly), and gate further work on this
   passing.

### Research engine (10–18)

10. **Clustering module** — Compute market-factor-residualized return correlations and produce
    point-in-time clusters of 5–30 names per refit.
11. **Johansen and Box-Tiao seeding** — Implement both eigenvalue-based cointegration seeders to
    produce starting weight vectors for NES on each cluster.
12. **NES optimizer** — Either hand-rolled separable NES or evotorch/pycma wrapper, with
    constraint handling for sparsity, gross exposure, and weight caps.
13. **Phase 1 fitness and orchestration** — Wire the composite phase 1 objective (capped ADF +
    amplitude-weighted 1σ excursions − half-life penalty) and run NES across all clusters in
    parallel.
14. **Phase 2a — trading policy optimizer** — Optimize entry/exit/stop thresholds, persistence
    filter, lookback window, sizing scalar, and asymmetric thresholds against the PnL +
    round-trip-count objective, weights frozen.
15. **Phase 2b — weight refinement with zeroing** — Optimize per-symbol [0, s_max] multipliers on
    phase 1 weights with the ADF cap as a hard constraint, sparsifying the basket against the same
    PnL objective.
16. **Walk-forward harness** — Run the full phase 1 + 2a + 2b pipeline on rolling 3-year-fit /
    6-month-OOS folds and produce per-basket OOS performance and stability diagnostics.
17. **Multiple-testing discount and survivor selection** — Apply a haircut to rank-ordered OOS
    results to filter the candidate pool down to baskets whose edge is statistically credible.
18. **Portfolio assembly** — Dedup baskets sharing names, correlation-check spread series, apply
    per-basket and per-name capital caps to produce the live stable of 20–50 baskets.

### Live trading (19–22)

19. **Interactive Brokers integration** — Wire ib_insync (or the official API) for account state,
    market data sanity checks, and order placement with bracket-style risk controls.
20. **Live signal and execution loop** — Daily post-close job that updates prices, marks open
    spreads, checks exit conditions, fires new entries on validated baskets, and emits IB orders
    for the next session.
21. **Position and PnL reconciliation** — Pull fills and positions back from IB, reconcile against
    expected state, persist trade history, and flag drift.
22. **Basket health monitoring and retirement** — Track rolling OOS hit rate, realized half-life,
    and round-trip frequency per live basket, with an automated retirement rule when behavior
    drifts out of band.

### Dashboard (23–25)

23. **Dash dashboard — research views** — Inspect clusters, candidate baskets, phase 1/2 optimizer
    output, and walk-forward fold results during research and refit cycles.
24. **Dash dashboard — live portfolio views** — Open positions, basket-level PnL, spread z-scores,
    time-to-stop, capital utilization, and basket health flags for daily monitoring.
25. **Dash dashboard — manual optimization controls** — UI for triggering refits, adjusting
    per-basket capital caps, force-closing or retiring baskets, and reviewing pending orders before
    they go to IB.

### Operations (26–27)

26. **Quarterly refit pipeline** — Scheduled job that re-runs clustering → phase 1 → phase 2a →
    phase 2b → walk-forward → portfolio assembly and produces a refit diff for human review before
    promoting to live.
27. **Operational hardening** — Logging, alerting, secrets management, daily backup of state, and a
    documented runbook for the common failure modes (IB disconnect, Databento gap, basket blowup,
    refit regression).