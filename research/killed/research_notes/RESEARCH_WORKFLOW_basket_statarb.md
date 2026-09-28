# Research Workflow — Basket Stat-Arb (Cointegrated Baskets)

Per-candidate research workflow for the cointegrated-baskets mean-reversion strategy. Follows the
standard nine-step template (framing → universe/data → signal → IS backtest → sensitivity → cost
stress → OOS one-shot → paper → pre-live → live small) with strategy-specific framing and a fresh
open-decisions list.

**Status:** Open Decisions #1–#4 locked (2026-06-17). **Machinery step complete and validated on
the known-good GOOG/GOOGL harness** (§9); Open Decisions #5–#8 resolved as the code forced them. No
real candidate cluster touched yet — the IS/OOS one-shot (Steps 4–7) is the next, separate task.

**Relationship to other docs**
- `basket_statarb_proposal.md` — the collaborating researcher's full production-scale proposal.
  This workflow is the **lightweight first test** of that proposal's *core* hypothesis, not an
  implementation of the full stack.
- `MULTI_STRATEGY_REFACTOR.md` — the interface/engine groundwork that lets PaperStreet *express
  and validate* a basket. Stage 1 of this workflow depends on Phase 3 (N-symbol engine); Stage 0
  deliberately does not.
- `STRATEGY.md`, `BACKTESTING.md` — the existing strategy interface, validation-gate, and
  benchmarking conventions this workflow inherits.

---

## 0. The reframe (why this doc exists)

The researcher's proposal is built for **production-scale systematic mining**: Databento minute
data, a numba simulation kernel, NES/CMA-ES optimization, walk-forward at scale, a
multiple-testing discount, and a Dash dashboard. Almost all of that machinery exists to answer the
**scaling** question — *can you systematically find 20–50 robust baskets out of hundreds of
names?*

That is the **second** question. The **first** question — the one that gates everything — is:

> **Does the core effect even exist?** Within an economically-related cluster, is there a weighted
> combination of 3–8 names whose spread mean-reverts *tradeably* (net of real costs) on a 2–15 day
> horizon, **out of sample**?

You can test the first question with PaperStreet's existing pandas/numpy/SQLite stack plus
`statsmodels`, on daily IBKR bars, with **no** optimizer, **no** Databento, and **no** new
package. If the core effect is absent on a handful of principled, hand-picked baskets, the
production-scale mining stack is not worth building. If it's present, you've earned the heavier
stack with evidence.

This mirrors the discipline that parked `spy_short_reversal`: validate cheaply, pre-commit the
kill criteria, run the OOS one-shot honestly, and let a negative result kill the candidate.

---

## 1. Framing

### Economic rationale

Two (or more) economically-related stocks share common factor exposure; idiosyncratic
divergences in their *weighted combination* (the spread) tend to revert as the shared economics
reassert. The edge is a liquidity/attention premium for warehousing temporary dislocation, not a
latency game — which is exactly why it can live at a multi-day horizon PaperStreet can trade.

### Why it might *not* work (state this up front)

- **Crowding / decay.** Classical pairs and basket stat-arb is among the most-published, most-mined
  effects in equities. Returns to the canonical version have decayed substantially since the
  mid-2000s. A textbook approach should be assumed crowded; the OOS result must do real work.
- **Cointegration is not constant.** Relationships break structurally (M&A, business-model
  divergence, sector rotation). Averaging into a broken spread is the classic stat-arb death.
- **Weight instability.** Estimated cointegrating vectors are notoriously unstable across windows;
  an in-sample mean-reverting combination can be noise that doesn't persist.

### Signal decay, capacity, expected magnitude

- **Horizon:** 2–15 day half-life (the tradeable band). Faster → latency game (out of scope);
  slower → capital sits idle and regime risk dominates.
- **Capacity:** ample at this account size — liquid large-caps, fixed share quantities, episodic
  trading. Capacity is not the binding constraint; *finding real edge* is.
- **Expected magnitude (calibration, not a target):** if the effect is real and un-crowded enough
  to survive, expect a *modest* per-basket OOS Sharpe — order of 0.5–1.0 net of costs — and
  episodic activity (a handful of round trips per basket per year). Treat a daily-bar,
  survivor-biased backtest that prints Sharpe ≫ 1.5 as a **red flag for overfitting/bias**, not a
  triumph.

### The most likely ways this backtest will lie to you

1. **Weight-selection overfitting** — picking the cointegrating vector that happened to revert
   in-sample. *Defense:* weight-stability screen + strict OOS one-shot + round-trip-count floor.
2. **Survivorship bias** — IBKR's universe is survivors. A spread that "always reverts" may be a
   relationship that simply didn't blow up *in this sample*. *Defense:* treat any positive Stage-0
   result as **provisional**, pending a delisted-inclusive redo in Stage 2.
3. **Gap-through stops** — daily bars tempt you to fill stops at the stop level; real overnight
   gaps blow through. Mean-reversion loss lives in the stops, so this flatters drawdowns. *Defense:*
   gap-conservative stop fills (next-session open, not the stop level).
4. **Multiple testing** — trying several clusters and reporting the best. *Defense:* pre-commit the
   cluster list and report **all** of them; apply a conservative bar.
5. **Cost omission on shorts** — borrow cost and dividends-on-shorts flatter the short legs.
   *Defense:* include both (Section 6).

### Pre-commitment

The IS/OOS split boundary, the exact cluster list, the trading-policy defaults, and the numeric
pass/fail gates (Section 7) **must be written down and frozen before any OOS data is touched.**
No re-tuning on OOS. This is the same one-shot discipline applied to `spy_short_reversal`.

---

## 2. The staged plan

| Stage | Scope | New infra | Gate to advance |
|---|---|---|---|
| **0 — Proof of concept** | Eigenvector baskets, 3–5 hand-picked clusters, daily bars, fixed policy, single IS/OOS split, `research/` notebook | `statsmodels` only | Any basket shows credible OOS edge net of costs (Section 7 gates) |
| **1 — Formalize** | Route validation through PaperStreet's **N-symbol engine**; rolling walk-forward; proper cost model; widen universe a little | Multi-strategy refactor **Phase 3**; `BasketStrategy(BaseStrategy)` | Edge survives walk-forward across multiple folds with stable weights |
| **2 — Scale** | The researcher's full proposal — systematic clustering, NES, multiple-testing discount, Databento (survivorship-corrected + minute execution realism), portfolio assembly | The full `data/`/`spread/`/`optimize/`/`validate/`/`portfolio/` stack | (Separate decision; only reached if Stage 1 holds) |

Stage 0 is the subject of this document. Stages 1–2 are sketched so the boundary is explicit.

---

## 3. Step-by-step (Stage 0)

### Step 1 — Framing → see Section 1. Freeze kill criteria before proceeding to Step 7.

### Step 2 — Universe & data

- **Data:** IBKR **daily** bars on the **`ADJUSTED_LAST`** basis (total-return), via the existing
  `market_data` cache (`what_to_show` is already configurable end-to-end — see `IBKR_NOTES.md`).
  Pre-warm each symbol so iteration is offline.
- **Universe:** 3–5 **hand-picked, economically-related clusters**, 5–15 liquid, **borrowable**
  names each (e.g. gold miners; money-center banks; integrated majors; large semis; airlines).
  Hand-picking substitutes for the proposal's residualized-correlation clustering at this stage —
  you are testing the *mechanism* on the most likely candidates, not mining the universe.
- **Exclusions:** drop hard-to-borrow names (you will be short half the basket).
- **Known gap:** the universe is survivors-only (no delisted names). Logged as a Stage-0 limitation
  (Section 8); it is a *tailwind*, so a negative result is still informative.

### Step 3 — Signal construction (`research/` notebook)

All discovery is on **daily** data (minute resolution buys nothing for multi-day equilibrium
estimation and adds microstructure noise).

1. **Cointegrating vector → basket weights.** Use **Johansen** (`statsmodels` `coint_johansen`,
   first cointegrating vector) and/or **Box-Tiao** (most-predictable direction; a small numpy
   generalized-eigenvalue computation). Use the eigenvector **directly** as the weights — **no NES
   refinement.** This is lighter *and* better epistemics: it removes the optimizer degrees of
   freedom that are the prime overfitting risk. (See Open Decision #1 on raw- vs. log-price
   estimation, which matters precisely because there is no NES step to bridge log→raw.)
2. **Spread & z-score.** Construct the spread as the weighted combination in **raw prices** (fixed
   share quantities, no rebalancing — matches how it would trade). Z-score on a rolling lookback
   (Open Decision #6).
3. **Screens (cheap, keep these):**
   - **ADF** (`statsmodels` `adfuller`) on the spread — confirm stationarity.
   - **OU half-life** — from an AR(1) fit on the spread (regress Δspread on lagged spread);
     require the 2–15 trading-day band.
   - **Weight stability** — fit the cointegrating vector on two adjacent sub-windows and require
     high cosine similarity (e.g. ≥ 0.9). This is the one piece of the `validate/` layer worth
     keeping in Stage 0, because instability is the dominant failure mode for raw eigenvector
     baskets.

### Step 4 — In-sample backtest (with pre-committed kill criteria)

- **Mechanics (research tooling, not the validation gate):** a daily spread simulation in the
  notebook. Per-basket state machine: flat → enter long-spread at `z ≤ −z_enter` (spread cheap) /
  short-spread at `z ≥ +z_enter`; exit at `|z| ≤ z_exit`, z-stop at `z_stop`, time-stop at
  `time_stop_mult × half_life`. Fixed share quantities scaled to a target notional.
- **This notebook sim is `research/`-tier**, not a second validation engine — consistent with
  `ROADMAP.md` → "Decided Against: vectorized/third-party backtesters." The **formal** validation
  gate is PaperStreet's own engine in **Stage 1**.
- **Benchmark / null.** A dollar-neutral basket has no buy-and-hold benchmark; the null is **zero**.
  The bar is a meaningfully-positive Sharpe **net of costs**, **plus** a round-trip-count floor so
  the result is not one lucky in-sample excursion (the researcher's own overfitting defense —
  borrow it).
- **Kill criteria** are committed here, before Step 7. Candidate parameters: min round trips over
  the window, OOS Sharpe threshold, max-drawdown ceiling, OU half-life OOS-vs-IS tolerance, weight
  stability threshold. Fill in concrete numbers in Open Decision #4 before looking at OOS.

### Step 5 — Parameter sensitivity

- Vary the **trading-policy** params (`z_enter`, `z_exit`, `z_stop`, `time_stop_mult`, lookback)
  around the fixed defaults and confirm a **plateau**, not a spike. Most of these only choose
  *which* excursions to trade and don't change the spread series — but **lookback** and any
  **sizing scalar** do change it, so recheck ADF/half-life when they move.
- A candidate whose edge sits on a knife-edge of one parameter setting is overfit; reject it.

### Step 6 — Cost stress

Include, and stress, **all** of:
- **Commission** — the existing IBKR per-share model.
- **Slippage** — half-spread per side (negligible for liquid large-caps at this size, but include
  it).
- **Borrow cost on shorts** — flat annualized bps × short notional × days held. Tens of bps for
  GC large-caps; this is why HTB names are excluded. Stress upward and confirm survival.
- **Dividends on shorts** — long legs receive, short legs pay. Treatment needs care given the
  `ADJUSTED_LAST` signal basis (Open Decision #5). If the edge only survives ignoring borrow +
  dividends, it is dead.

### Step 7 — OOS one-shot

- Run the **frozen** spec (weights, policy, costs) once on the held-out window. **No re-tuning.**
- Apply the Section-4 kill criteria as a pass/fail gate, exactly as committed.
- Report **every** pre-committed cluster, not just the winner (multiplicity discipline).
- A pass advances to **Stage 1**; a fail parks the candidate with the reason recorded, the way
  `spy_short_reversal` was parked.

### Step 8 — Paper trading

Only reachable via Stage 1 (a basket must run as a real `BasketStrategy` through PaperStreet's
N-symbol engine and live loop first). Multi-leg submission, exposure aggregation, and the
borrow/dividend cost model in live form are prerequisites — see `MULTI_STRATEGY_REFACTOR.md`
Phases 2–4 and `RISK.md`.

### Step 9 — Pre-live checklist → live small

Standard pre-live gate (per `RISK.md`): system-wide order-size cap, kill switch, exposure check,
and PDT-floor headroom all in place. Multi-leg atomicity policy resolved (`MULTI_STRATEGY_REFACTOR.md`
Open Decision #4). Size the first live basket against the **$25k PDT floor**, not starting equity:
a long/short basket's **gross** exposure is roughly 2× its net, so size so the worst plausible
joint drawdown leaves equity comfortably above the floor.

### Stage-0 output — anticipate the basket spec

Stage 0's surviving baskets are the input to Stage 1, so write them out in the rough shape of the
eventual **basket spec** — the artifact that will later cross the research→execution seam — so the
Stage 0 → Stage 1 transition is a *load*, not a rewrite. One spec = one validated basket:

- **Identity** — basket id / name, and the cluster it came from.
- **Constituents & weights** — the symbols and their **signed share quantities** (long/short),
  scaled to the per-basket target notional.
- **Trading policy** — `z_enter`, `z_exit`, `z_stop`, `time_stop_mult`, z-score lookback, sizing
  scalar (the frozen Stage-0 defaults).
- **Provenance** — estimation window, IS/OOS boundary, OOS metrics (Sharpe, max drawdown,
  round-trip count), OU half-life (IS and OOS), ADF stat, weight-stability score, data basis
  (`ADJUSTED_LAST`), and a status field (`candidate` / `validated` / `parked`).

Persist these as a small structured record (JSON or a DB row) per surviving basket — not buried in
notebook state. This is **only the shape convention**, not the full handoff-contract decision: where
the research engine that emits specs lives, and the `BasketStrategy` that consumes them, are a
Stage-1 concern with their own decision note (deferred until Stage 0 passes). The point here is
just that Stage 0 should produce specs Stage 1 *can* consume.

---

## 4. Design decisions (settled for this workflow)

These are the lighter-version choices for Stage 0. They are deliberate and should not be silently
re-litigated; challenge with reasoning if you disagree.

1. **Eigenvector weights, no NES.** Johansen/Box-Tiao vector used directly. Lighter, and removes
   the optimizer's overfitting surface. The light version is a **strong positive test** (if it
   works, the thesis is alive) and a **soft negative test** (if it fails, the effect may require
   the heavy stability-aware optimization — which you should then approach with heightened
   skepticism, not enthusiasm).
2. **Daily IBKR bars, no Databento.** All multi-day equilibrium estimation is daily anyway.
   Minute-level execution realism is deferred to Stage 2.
3. **Fixed trading policy, no Phase-2a/2b optimization.** Testing whether the *spread* has edge,
   not finding the optimal policy.
4. **Hand-picked clusters, no clustering module.** Testing the mechanism on likely candidates.
5. **Keep the weight-stability screen.** The one `validate/` piece retained, because instability
   is the dominant Stage-0 failure mode and it costs a few lines.
6. **Notebook sim for discovery; PaperStreet engine as the gate (Stage 1).** Preserves the single-
   validation-gate principle.
7. **Single IS/OOS split first.** Rolling walk-forward is a Stage-1 upgrade.

---

## 5. Known limitations (Stage 0)

- **Survivorship bias** — survivors-only universe; inflates results. Any positive result is
  provisional until a delisted-inclusive redo (Stage 2).
- **No intraday execution realism** — daily bars cannot model intraday stop fills or overnight
  gap-through-stop. Mitigated, not solved, by gap-conservative stop modeling; resolved only with
  minute data (Stage 2).
- **Raw-eigenvector weight instability** — addressed by the stability screen, but not eliminated.
- **Multiplicity** — only a few clusters, so the formal multiple-testing discount is deferred;
  managed by pre-committing the cluster list and reporting all.
- **Dividend treatment is approximate** — see Open Decision #5.

---

## 6. Open decisions

### Resolved (locked — do not re-litigate; challenge with reasoning if you disagree)

**#1 — Price basis: raw prices throughout.**
Johansen and Box-Tiao estimated on raw prices; spread constructed as `Σ wᵢ × Pᵢ(t)` in raw
prices; z-score and sim all in raw-price space. Box-Tiao is the primary vector; Johansen (raw) is
the cross-check. Rationale: the basket trades fixed share quantities, so the P&L is a linear
combination of raw prices. Estimating on log prices would optimize a different spread than the one
being traded; the NES refinement step that bridged this in the researcher's full stack is not
present in Stage 0. ADF confirmation is the real stationarity check — do not lean on Johansen
p-values for inference given the raw-price deviation from the classical assumption.

**#2 — IS/OOS split structure: 5-year IS / 2-year OOS.**
Exact boundary date locked **after** Claude Code confirms data availability for all pre-committed
clusters, **before** any OOS look. Approximate target: IS ends ~2022, OOS covers ~2023–present,
giving a 5-year IS window from ~2017. The IS window deliberately includes the 2020 COVID crash —
a relationship that only holds in calm markets is not worth trading. The weight-stability screen
is the mechanism that catches baskets whose vectors shift across that break.

**#3 — Cluster list (4 clusters, pre-committed).**
Report all four. Cherry-picking the winner is not permitted.

| Cluster | Names | Economic rationale |
|---|---|---|
| A — Ad-driven internet | META, GOOGL, GOOG, SNAP | Digital ad spend; GOOGL/GOOG known-good pair embedded |
| B — Money-center banks | JPM, BAC, C, WFC | Rates, credit cycle, yield curve |
| C — Cloud / enterprise software | MSFT, AMZN, CRM, ORCL | Enterprise IT spend, cloud migration cycle |
| D — Large semis | NVDA, AMD, INTC, AVGO, QCOM | Data center / PC / mobile end-markets |

Operational notes:
- SNAP: verify borrow availability and rate before running; swap for PINS if HTB.
- INTC: a known structural break (business-model crisis vs. NVDA/AMD) is deliberately included to
  test whether the weight-stability screen catches it. If it does, that's the screen working. If
  you'd rather not have a known-broken name in a Stage-0 cluster, swap for MU or TXN.
- GOOGL/GOOG overlap with Cluster A and known-good harness is intentional: it makes the Step-3
  screen results more interpretable.

**#4 — Pass/fail gates: three-zone structure.**
Rationale for three zones rather than a single threshold: this is the first run of daily-bar
stat-arb in PaperStreet; there is no calibrated prior for what "a good result looks like" on this
specific setup. The structural gates are hard (mechanism-presence tests with principled thresholds);
the Sharpe zone boundary is honest about uncertainty. A weak pass is not a free pass — it advances
to Stage 1 with explicit skepticism logged and Stage 1 treated as the real test, not a formality.

**Structural gates (hard — any failure is an automatic park regardless of Sharpe):**

| Gate | Threshold | Rationale |
|---|---|---|
| IS ADF p-value | ≤ 0.05 | Spread must be stationary in-sample to proceed at all |
| IS OU half-life | 2–15 trading days | Outside this band: either not mean-reverting or too slow to trade |
| Weight stability (cosine similarity, adjacent IS windows) | ≥ 0.85 | Unstable vector = relationship isn't real or isn't persistent |
| Min IS round trips | ≥ 25 | Below this the IS result is one or two excursions, not evidence |
| Min OOS round trips | ≥ 10 | Below this the OOS result has no statistical content |
| OOS OU half-life vs IS | ≤ 2× IS half-life | Spread reverting 2× slower OOS signals a drifting relationship |

**Sharpe / drawdown zones (applied after all structural gates pass):**

| Zone | OOS Sharpe (net of all costs) | OOS Max Drawdown | Action |
|---|---|---|---|
| **Strong pass** | ≥ 0.8 | ≤ 20% of peak notional | Advance to Stage 1 with confidence |
| **Weak pass** | 0.3–0.8 | ≤ 25% of peak notional | Advance to Stage 1 with explicit skepticism logged |
| **Fail** | < 0.3 | > 25%, or any structural gate missed | Park with reason recorded |

**Reporting discipline:** all four clusters reported. A single weak-pass cluster out of four is a
weak result; two or more passing independently is meaningfully stronger. The readout itself is
valuable independent of zone — half-life distribution, Sharpe variance, which screen binds —
because it calibrates the prior for Stage-1 gates.

---

### Still open (flag, don't guess; surface when a step forces the question)

5. **Dividend-on-shorts accounting on an `ADJUSTED_LAST` basis.** The adjusted series already
   reinvests dividends into price, but fixed-share trading realizes them as real cash flows
   (received on longs, paid on shorts). Options: (a) model per-leg dividend cash flows explicitly
   using a split-only series plus a dividend schedule; (b) restrict Stage-0 clusters to
   similar-yield names so the net effect is small and flag it as a known approximation.
   *Leaning:* (b) for Stage 0 — the pre-committed clusters are predominantly low-yield growth
   names where the net dividend effect is small; flag it and fix in Stage 1.

6. **Z-score lookback definition.** Fixed window (e.g. 60 trading days) vs. a multiple of the
   estimated half-life. Affects the spread series itself, so recheck ADF and half-life under
   whichever is chosen. Surface in the machinery step; pick before any IS evaluation.

7. **Per-basket target notional.** Size given the $50k account, the $25k PDT floor, and gross
   exposure ≈ 2× net on a long/short basket. One basket should not threaten the PDT floor alone
   on its worst plausible drawdown. Surface when the sim is running and real P&L numbers exist.

8. **Stage-0 tooling boundary.** Research-tier notebook sim as the discovery tool, with the
   PaperStreet engine as the Stage-1 validation gate — versus waiting for Phase 3 and doing
   everything in-engine from the start. *Leaning:* notebook first; already reflected in the staged
   plan. Confirm in the Claude Code machinery step.

---

## 7. Non-goals (Stage 0)

Explicitly **not** built here — these are Stage 1/2:

- NES / CMA-ES optimization (phase 1 shape, phase 2a policy, phase 2b zeroing).
- Databento ingestion, parquet store, corporate-action pipeline, minute-level simulation.
- Systematic residualized-correlation clustering and the PIT universe builder.
- Walk-forward at scale and the multiple-testing discount machinery.
- Portfolio assembly / dedup / capital caps / basket retirement (20–50 baskets).
- The `BasketStrategy(BaseStrategy)` live implementation and multi-leg order submission.
- Any dashboard.

---

## 8. Next action

Open Decisions #1–#4 are resolved (Section 6 above). The first implementation step is a
**Claude Code** task: build the Stage-0 `research/` machinery and validate it on the known-good
GOOG/GOOGL harness **before** touching the pre-committed clusters. The real-candidate one-shot
(Steps 4–7, IS backtest through OOS gate) is a **separate later Claude Code prompt**, issued only
after the machinery step is clean and reviewed. Open Decisions #5–#8 are resolved during the
machinery step as the code forces them.

---

## 9. Machinery step — status & known-good readout

The Stage-0 machinery is built as a proper module (`research/killed/basket_statarb/`, not a notebook) and
validated on the known-good basket. Implements Steps 2–3 (data, weights, spread, screens) and the
Step-4 research-tier sim; **no candidate cluster, IS/OOS split, or gate is run here** — those are
Steps 4–7 (separate task).

### Modules (`research/killed/basket_statarb/`)

| Module | Responsibility |
|---|---|
| `cointegration.py` | Box-Tiao (primary) + Johansen (cross-check) → normalized weight vectors. Eigenvector used **directly**, no NES. **Raw basis (#1 locked)**; `price_basis` kept general. Only Johansen's eigenvector is used, never its p-values (#1). |
| `spread.py` | Raw-price share-weighted spread, rolling z-score, ADF, OU half-life (AR(1) Δs fit), weight-stability cosine similarity. |
| `costs.py` | IBKR commission, half-spread slippage, borrow on shorts, dividends-on-shorts (#5 still open — flat-yield approximation, default 0, flagged). |
| `sim.py` | Per-basket daily long/short-spread state machine; fixed shares; gap-conservative next-open stop fills. **Research discovery tool, NOT the validation gate** (#8) — `backtesting/` engine is the gate at Stage 1. |
| `data.py` | Cache-first daily `ADJUSTED_LAST` loader + one-time serialized TWS prewarm. |
| `known_good.py` | The harness below. |
| `tests/test_basket_statarb.py` | 14 hermetic synthetic tests: cointegration recovers a known vector, half-life recovers a known OU half-life, ADF/z-score behave on stationary vs. random walk, sim round-trips a constructed spread. |

Resolved-during-machinery decisions: **#6** z-score lookback — fixed 60 trading days (parameterized,
revisit before IS eval); **#8** tooling boundary — research sim for discovery, `backtesting/` engine
as the Stage-1 gate. **#5** (dividends) and **#7** (per-basket notional) remain open as the doc
intends; both are parameterized (`dividend_yield_annual_bps=0`, `gross_notional=$10k`). New
dependency: **statsmodels** (Johansen + ADF), not yet pinned in `requirements.txt`.

### Known-good readout

Full-history daily `ADJUSTED_LAST`, lookback 60, sim policy
`z_enter=2.0, z_exit=0.5, z_stop=3.5, time_stop=3×HL, gross=$10k`,
costs `0.005/sh ($1 min) + 1bp half-spread + 50bp borrow + 0 div`. Both estimators on the locked
**raw** basis (Box-Tiao primary, Johansen cross-check):

```
=== KNOWN-GOOD (expect: low ADF p, half-life ~2-15d, stable weights) ===
pair         method    basis     n    ADF p  half-life   wstab  trades       net$     gross$    costs$   win%
GOOG/GOOGL   box_tiao  raw    3075   0.1116       10.6   1.000      83       -537        725      1262     55
GOOG/GOOGL   johansen  raw    3075   0.0996       10.7   1.000      85        -96       1212      1308     58

=== UNRELATED CONTROLS (expect: high ADF p and/or unstable weights) ===
pair         method    basis     n    ADF p  half-life   wstab  trades       net$     gross$    costs$   win%
DUK/REGN     box_tiao  raw    3769   0.0953      247.8   0.979      80      -6436      -4875      1560     59
DUK/REGN     johansen  raw    3769   0.0950      269.2   0.943      73     -40188     -38580      1609     53
KO/NVDA      box_tiao  raw    3769   0.0976      188.8   0.587      76       9822      11115      1294     70
KO/NVDA      johansen  raw    3769   0.9605     3798.8   0.583      75       7195       8427      1232     68
XOM/BIIB     box_tiao  raw    3769   0.0394      124.5   0.730      81      15387      16530      1143     62
XOM/BIIB     johansen  raw    3769   0.0399      124.8   0.715      79      14528      15667      1139     62
```

### Interpretation — machinery validated

On the locked raw basis, Box-Tiao and Johansen **agree closely** (the proposal's expectation): for
GOOG/GOOGL both give ADF ≈ 0.10, half-life ≈ **10.6 trading days** (in the 2–15d band), and
weight-stability **1.000**. The known-good basket separates from the controls, but the discriminators
are the **half-life band and weight-stability, not ADF p alone**:

1. **Weight stability** — GOOG/GOOGL = **1.000** (dual-class hedge ratio ≈ (−1, +1), mechanically
   fixed) vs. controls that churn (KO/NVDA 0.58, XOM/BIIB 0.72).
2. **Half-life band** — GOOG/GOOGL ~**10.6d**; every control is **124–3799d**, an order(s) of
   magnitude out of band.
3. **ADF p alone is spurious** — XOM/BIIB (energy vs. biotech, no economic link) posts the *lowest*
   ADF p in the table (≈ 0.04), beating GOOG/GOOGL. Textbook spurious cointegration — exactly why
   the §6 structural gates are a *set* (ADF **and** half-life **and** weight-stability **and**
   round-trip floors), not ADF in isolation. The machinery reproduces the failure mode the gate set
   is built to catch.

Per §1, GOOG/GOOGL's small/basis-dependent **sim PnL is not a failure** (−$537 Box-Tiao, −$96
Johansen after ~$1.3k costs): it is a very tight spread (σ ≈ $1 on ~$150 names). Stage 0 validates
statistical **detection**; tradeable amplitude is what the candidate clusters will test. Note also
that GOOG/GOOGL's own ADF ≈ 0.10 would *not* clear the §6 candidate gate of ADF ≤ 0.05 — expected,
since the known-good test is about relative separation and detection, not certifying a tradeable
basket.

Caveat for Stage 1: GOOG (Class C) `ADJUSTED_LAST` begins at its 2014 inception and the GOOG/GOOGL
raw ratio runs 0.50 → 0.99 across the window — an adjustment/inception seam, not a tradeable
dislocation. The PIT universe and two-series (adjusted + split-only) handling matter once PnL, not
detection, is the question.

### Reproduce

```bash
# one-time prewarm (TWS on paper port 7497):
python -m research.killed.basket_statarb.data PREWARM GOOG GOOGL DUK REGN KO NVDA XOM BIIB "15 Y"
# offline thereafter:
python -m research.killed.basket_statarb.known_good     # side-by-side readout
pytest tests/test_basket_statarb.py -q           # synthetic primitive tests
```
