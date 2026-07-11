# Research Workflow — Diversified Trend on CME Futures

> **STATUS: STOPPED at Step 3 — documented NEGATIVE RESULT (2026-06-21).** The
> 8-leg full-size TSMOM candidate (ES, ZN, 6E, 6J, 6A, CL, GC, HG) is **killed as
> designed.** It cleared the load-bearing gate only by +0.052 Sharpe, and the
> drought-core confirmation (Step 3 sub-window, below) showed the entire gate-window
> Sharpe is a 2011-2015 secular artifact: **2016-2019 drought-core net Sharpe 0.019
> with a −21.57% drawdown** — zero return in the regime that matters while still
> breaching the −20% live floor. **OOS (2023-present) stays QUARANTINED — never
> opened.** No parameter sweep, no universe trimming (that would be hindsight
> selection + OOS contamination). Code preserved as a negative result, not deleted.
> The thesis (diversified trend at firm scale) is not refuted; *this specific 8-leg
> design* is. Re-entry would require a different universe/signal pre-committed on
> economic grounds, evaluated on fresh data. See the 2026-06-21 KILL entry in the
> progress log and the "Kill rationale" section below.
>
> *(Was: ACTIVE, resumed 2026-06-19 under a rebased $5M capital assumption.
> Supersedes the parked `diversified_trend_strategy_notes.md`. Selected as the
> proof-of-concept first firm-scale strategy because diversified futures trend is
> the one medium-frequency edge with decades of multi-firm OOS evidence and the
> correlation structure equities cannot provide.)*

Living document. Update as decisions are made.

> **Capital note (added 2026-06-22):** every $5M figure below is this strategy's
> large-capital research track, not the global default. `BacktestConfig.starting_cash`
> reverted to $50k after the kill — see `RISK.md` → Capital Tracks. The 2026-06-19
> "global default = $5M" line in Open Decisions records the state at that time and is
> superseded.

> **Step numbering (canonical).** The section headers below are authoritative:
> 0 Framing · 1 Universe & Data · 2 Signal · 3 In-Sample Backtest · 4 Parameter
> Sensitivity · 5 Cost Stress · 6 OOS · 7 Paper · 8 Pre-Live · 9 Live. The code
> filenames are numbered off-by-one because they count the continuous-contract
> *build* as its own step: **`build_continuous.py` = data load/build (part of
> Step 1)**, **`step3_signal.py` = Step 2 (Signal)**, **`step4_backtest.py` =
> Step 3 (In-Sample Backtest)**, **`step4_gate_diagnostics.py` = Step 3 gate
> diagnostics**. The parameter sweep is **Step 4**; cost stress is **Step 5**.
> Earlier progress-log entries that called the IS backtest "Step 4" used the
> filename scheme and have been re-annotated to this canonical scheme.

---

## Why this was un-parked

Two things changed since the 2026-06-13 park:

1. **Capital rebased $50k → $5M (explicit parameter, not a backtest fiction).**
   The $50k account made integer-contract granularity the binding constraint —
   one MES is ~60% of a $50k account, so fine vol-weighting across a basket was
   impossible and the diversification (the strategy's entire reason to exist)
   could not be realized. $5M sits in the clean band where **neither granularity
   nor capacity binds**: full-size liquid contracts at proper vol-weights across
   enough instruments, and $5M is immaterial to futures capacity (which bites in
   the hundreds of millions). PDT does not apply to futures and is moot at $5M
   regardless. **Discipline:** capital is a documented config parameter; live
   sizing must match live capital. Validating at $5M and deploying less would
   make the backtest lie about achievable sizing — that is not what this is.

2. **The production multi-symbol engine is NOT a prerequisite for Steps 2–5.**
   The original park reason ("blocked on a portfolio backtester") conflated the
   research question with the production build. A **vectorized research notebook**
   (load N continuous series, align, signal, inverse-vol weight, integer-round,
   aggregate P&L, subtract roll/costs) validates the thesis with zero changes to
   the production engine. The production multi-symbol/portfolio engine is a
   downstream build **gated on Steps 2–5 passing** — the same "kill cheap before
   building infra" discipline applied to basket stat-arb.

---

## Strategy Summary

**Type:** Time-series momentum (trend following) on a diversified basket of CME
futures (full-size where capital allows, micros for granularity).

**Holding period:** Multi-day to multi-week. Hold through overnights and
weekends with conservative vol-target sizing.

**Capital:** $5,000,000 base. Risk limits designed against a drawdown budget on
base equity, **not** against a PDT floor (N/A for futures).

**Known headwind:** The 2024–2028 US policy regime may be hostile to trend
(mean-reverting policy-announcement vol, weekend gap risk). Accepted as a
**regime risk**, not a design driver. The evaluation window must span at least
one regime change.

---

## Step 0 — Lock the Framing

### Thesis

Time-series momentum captures slow-moving drift across asset classes driven by
underreaction to fundamentals, behavioral anchoring, and risk transfer from
hedgers to speculators. It has persisted out-of-sample for decades across
instruments and is not arbed away because the drawdowns are too deep and too
long for most institutional capital to hold through. It is expected to keep
working in the long run; the current policy regime may be a multi-year headwind.
Size to survive a worse-than-historical drawdown.

**Who is on the other side:** hedgers transferring risk (commercial producers,
asset managers de-risking mechanically); discretionary traders fading "extended"
trends.

### Constraints

- Hold through overnights: **yes**. Hold through weekends: **yes**, conservative sizing.
- Universe: diversified across equity / rates / FX / commodity. (Rates and FX are
  now **in v1** — at $5M the granularity reason for deferring them is gone, and
  they are the *most* diversifying legs, lowest correlation to equity.)
- Per-instrument risk: inverse-vol weighted to equal risk contribution; cap any
  single instrument's share of portfolio risk (e.g. ≤ 25–30%).

### Success / Kill Criteria (committed before research begins)

| Phase | Criterion | If met | If not |
|---|---|---|---|
| IS | Diversified basket Sharpe (net) **beats best single instrument** in the basket | Diversification adds value → continue | Diversification not paying for itself → reconsider universe or kill |
| OOS | Sharpe ≥ 0.4 net of realistic costs | Proceed to paper | Kill or document why OOS is uninformative |
| OOS | Max drawdown ≤ 25% of base equity | Proceed to paper | Kill or revisit sizing |
| Paper | 3+ months not catastrophically worse than OOS | Proceed to small live | Extend / investigate / kill |
| Live | Drawdown exceeds worst IS/OOS DD by a pre-set margin, or hits a hard −20% of base | **Kill / halt** | — |
| Live | 18 months of live underperformance vs backtest by > 2 SE | Kill | — |

The IS "beats best single instrument" gate is the load-bearing one: it is the
direct test of whether diversification — the thesis — actually works at this
capital and universe.

> **Which number is the gate (reconciled 2026-06-21).** The committed gate is
> **beats-best-single**, above. The `>= 0.30` absolute hard-coded in
> `step4_backtest.py` and echoed in early progress-log entries is a *screening
> placeholder* — a cheap "is it even alive" floor — **not** the committed gate.
> The load-bearing gate was actually computed in `step4_gate_diagnostics.py`
> (Task 1): **basket 2011-2019 net Sharpe 0.481 vs best single instrument 6J
> 0.429 → PASS by +0.052.** It passes, but the margin is thin and leans on
> non-repeatable secular moves (see the 2026-06-21 progress-log entry); read
> that before treating the pass as decisive.
>
> **OUTCOME (2026-06-21): the thin pass did not survive the drought-core
> confirmation. Candidate KILLED.** See the "Kill rationale" section immediately
> below — the +0.052 margin is within noise and the whole gate-window Sharpe is a
> 2011-2015 secular artifact (drought-core Sharpe 0.019).

### Kill rationale — 8-leg full-size TSMOM candidate (2026-06-21)

Killed **as designed**, at Step 3, before any sweep or OOS. The decision rests on
four mutually-reinforcing facts, none of which a parameter sweep could repair
(sweeping would be optimization on the same IS data; trimming the universe on the
IS standalone Sharpes would be hindsight selection that also contaminates OOS):

1. **Diversification margin is within noise.** Basket 2011-2019 net Sharpe 0.481
   vs best single leg (6J) 0.429 — only **+0.052**. The basket of 8 adds ~0.05
   Sharpe over just holding one yen contract.

2. **Realized correlation eats the diversification benefit.** Basket Sharpe 0.481
   sits *below* the ~0.58 zero-correlation ideal for its own legs — realized
   cross-leg correlation in drawdowns erases the benefit the inverse-vol sizing
   assumed. Same root cause as the 10%→~13% realized-vol overshoot and the
   −23.89% worst DD breaching the −20% live floor: signals align in the tail, so
   the zero-correlation ex-ante scaling under-sizes risk exactly when it matters.

3. **The gate-window Sharpe is a 2011-2015 secular artifact (the decisive fact).**
   The drought-core sub-window confirmation (`step4_drought_core.py`, a strict
   slice of the existing IS run — no new test) splits the base case:

   | Sub-window | Net Sharpe | Net PnL | Worst DD |
   |---|---|---|---|
   | 2011-07..2015-12 secular (Abenomics yen slide + EZ bond bull) | **0.932** | $2.46M | −23.89% |
   | 2016-01..2019-12 **drought core** | **0.019** | $0.05M | **−21.57%** |
   | 2011-07..2019-12 full base case (gate window) | 0.481 | $2.51M | −23.89% |

   **2011-2015 is 98% of the base-case PnL.** In the drought core — the regime
   most analogous to the expected hostile 2024-2028 forward regime — the basket
   earns essentially nothing (Sharpe 0.019) while *still* drawing down −21.57%,
   past the −20% live floor. It carries full drought risk for zero drought return.
   The "2011-2019 conservative base case" was mislabeled: 2011-2015 contains two
   strong secular trends, not a drought.

4. **The engines are dead in the drought core (per-leg, `step4_gate_diagnostics.py`).**
   The two top return legs go to ≈0 in 2016-2019: 6J standalone Sharpe **−0.26**
   (earned 55% of its IS contribution in 2013-15 Abenomics + 28% in 2022 BoJ); ZN
   standalone Sharpe **0.03** (38% from the 2011-12 EZ bond bull + 39% from 2022
   rate hikes). The basket-level 0.019 above is the portfolio echo of this.

**What is NOT being concluded.** Trend following as a premium is not refuted — the
IS even shows crisis convexity (2020 Sharpe 1.90, 2022 1.33). What is refuted is
*this specific 8-leg, single-lookback, daily-resized design at $5M* as a forward
bet. Any re-entry must pre-commit a different universe/signal on economic grounds
and prove it on data this candidate never touched — not tune this one.

---

## Step 1 — Universe and Data

### Why futures (locked)

Cleaner access to non-equity assets; trend works on a diversified multi-asset
basket, not concentrated equity; 60/40 (§1256) tax treatment; no PDT; clean
shorts (no borrow/locate); 23-hour sessions partially absorb weekend gaps.

### Candidate v1 basket (pre-committed on economic span, NOT searched)

Target ~8–12 instruments spanning four sectors so no single sector dominates
risk. Exact list is an open decision; the principle is sector span, not
backtest selection.

| Sector | Instrument | Size | Notes |
|---|---|---|---|
| Equity index | ES | full | One equity bet; NQ/RTY dropped (≈0.8–0.9 corr) |
| Rates | ZN | full | One US-duration bet; ZF/ZB dropped (redundant tenors) |
| FX | 6E, 6J, 6A | full | Most-diversifying legs; EUR/AUD risk-on, JPY safe-haven |
| Commodity | CL, GC, HG | full | Energy / precious / industrial; NG dropped |

8 instruments, all full-size. At $5M / 10% vol each gets ~$170k/yr risk budget
(~4 ES, ~4 GC, more of the rest) — integer granularity fine, no micros needed.

Use full-size where $5M supports proper vol-weighting; micros only where a
full-size contract is too lumpy for the target weight.

### Data

- Continuous-contract series required; **roll methodology is decision #1**
  (recommend: ratio-adjusted for signals, unadjusted/actual for PnL).
- Source: probe IBKR daily futures depth first; fall back to a futures vendor
  (e.g. CSI) if depth/quality is insufficient. Parity-check if mixing sources.
- Cache to `market_data_bars` before research — no live pulls during iteration.

### Sample period

Data load: 2010-06-06 (Databento GLBX.MDP3 earliest) → present.
Warm-up: 2010-06 → 2011-06 consumed by the 12-month signal lookback; never evaluated.
IS: 2011-07-01 → 2022-12-31 (~11.5y). OOS: 2023-01-01 → present (~3.5y, untouched
until the OOS one-shot). Note: 2008 GFC is OUT of sample — deliberate, per Open
Decision #3.

Looking at OOS counts as using it. Any tweak it informs contaminates it.

---

## Step 2 — Signal Construction (vectorized notebook)

Notebooks only; no production code yet.

- **Per-instrument signal:** sign of trailing N-month return, N in 6–12m. (Commit
  an *ensemble* of lookbacks rather than one, to avoid single-lookback overfit.)
- **Direction:** +1 / −1 / 0 (0 only if explicitly flat).
- **Sizing:** inverse-vol weighted to equal risk contribution.
- **Rebalance:** weekly (Friday close → execute Monday open).
  > **DISCREPANCY (flagged 2026-06-21, to resolve before the sweep).** The locked
  > design says *weekly*, but `step4_backtest.py` actually re-sizes **daily**
  > (positions recomputed every bar, then `shift(2)`-lagged) and charges costs
  > only on signal *sign flips*, not on weekly re-weighting turnover. So the IS
  > result is a daily-rebalance result, and "daily" is absent from the Step 4
  > sweep grid. Resolve one way before sweeping: either (a) implement true weekly
  > rebalance to match the spec, or (b) accept daily re-sizing as the design and
  > add it to the grid. Until resolved, the rebalance axis is not being tested as
  > written.

**Before any backtest:** plot signal vs forward return per instrument; signal
persistence (flip frequency); behavior in 2008 / 2020 / 2022 / 2024–25. Confirm
it behaves as the thesis predicts before proceeding.

**Do NOT** add regime filters, stops (trend strategies generally don't), signal
combinations, or parameter tuning on the first pass.

---

## Step 3 — In-Sample Backtest (vectorized notebook, portfolio-level)

Do the full portfolio backtest in the notebook — all instruments aligned,
vol-weighted, **integer-rounded contracts** (no fractional-contract fantasy),
aggregated P&L net of roll and costs. The production single-symbol
`run_backtest` engine is **not** used here; it can't represent the portfolio.

### Sizing

- Target portfolio vol: 10% annualized (low end — keeps normal 20–30% trend
  drawdowns inside the 25% OOS / −20% live kill lines; Sharpe is invariant to the
  vol scalar, so lower target costs nothing in risk-adjusted terms).
- Weights: inverse-vol from PIT 60d realized vol, scaled by one factor to ex-ante
  10% portfolio vol under a zero-correlation assumption (σ_p ≈ sqrt(Σ wᵢ²σᵢ²), no
  covariance matrix). Report realized portfolio vol; the gap vs 10% is information.
- Per-instrument risk cap: 25% (natural share ~12.5%; cap is a backstop).
- Vol-estimate floor: floor each instrument's vol estimate at its trailing 1–2yr
  10th–20th percentile (or cap per-instrument leverage) so a collapsing vol estimate
  can't explode position size into a vol spike.

### Look at first

Equity-curve shape (smooth drift with deep, long drawdowns is normal); drawdown
depth/duration; trade frequency (low); per-instrument contribution (one
instrument dominating = red flag); regime behavior.

### Costs (aggressive, not optimistic)

| Item | Assumption |
|---|---|
| Commission | full-size ≈ $2.00–2.50 RT/contract (IBKR tiered); micros ≈ $0.85/side |
| Slippage | 0.5–1 tick/side (liquid core); wider for thin contracts (NG, far rates) |
| Financing | none (daily mark-to-market) |
| Exchange/reg fees | per-contract per CME schedule |

### Regime-setgmented discipline
Evaluation is regime-segmented, NOT pooled. Do not report a single full-sample
Sharpe as the headline.
- Base case (conservative): 2011–2019 trend drought. If the strategy can't survive
  this, it's not viable regardless of full-sample numbers.
- Stress checks: 2020 (COVID) and 2022 (inflation/rates) — confirm crisis-convexity
  behavior shows up in-sample.
- Anchor expected Sharpe / DD priors on the drought decade, not the pooled average.
- Inspect WHERE PnL originates (e.g. is rates PnL a non-repeatable secular-bull
  artifact?), not just how much.

---

## Step 4 — Parameter Sensitivity (NOT Optimization)

Sweep and read the **distribution** of Sharpes, never the max.

| Parameter | Sweep |
|---|---|
| Lookback (months) | 3, 6, 9, 12, 15, 18 |
| Rebalance | weekly, biweekly, monthly (+ **daily**, to bracket the as-implemented IS result — see Step 2 discrepancy) |
| Vol window | 30d, 60d, 90d |
| Vol target | 8%, 10%, 12%, 15% |

> **Vol-target is a drawdown-budget choice, not a Sharpe choice (noted 2026-06-21).**
> Sharpe is invariant to the vol scalar, so this sweep axis moves only DD, not
> risk-adjusted return. It matters because the IS base case already runs at
> ~13% *realized* vol (vs 10% target) and its worst DD −23.89% breaches the −20%
> live floor *at realized vol*. Lowering the target to 8% buys headroom "for
> free," but the overshoot is regime-dependent (signals align in drawdowns, so
> the zero-correlation ex-ante scaling under-sizes the tail). Prefer evaluating
> **correlation-aware sizing** (a covariance-based scalar, or a realized-vol
> governor) rather than only lowering the static target.

Broad plateau → real edge; single peak with degradation either side → overfit,
kill. Pick from the **middle of plateaus**; document the choice.

---

## Step 5 — Cost Stress

Take the Step 4 candidate, **double** commission and slippage. Survives with
degraded-but-acceptable Sharpe (e.g. 0.7→0.5) → probably real. Collapses
(0.7→0.1) → overfit to favorable costs, kill. Real fills include halts, fast
markets, dislocated Sunday opens.

---

## [Infrastructure Gate] — build the production multi-symbol engine only if Steps 2–5 pass

This is the heavy build, deliberately deferred until the thesis has earned it.
Scope (to spec then, not now): cross-instrument data alignment and calendars;
point-in-time per-instrument vol; portfolio-level position accounting and risk;
futures roll handling; SPAN-style margin; live-parity fills. Until Steps 2–5
pass, **do not build it.**

---

## Step 6 — Out-of-Sample (ONE SHOT)

Run the locked candidate on 2023–present via the production engine. One shot, no
tweaking.

**Acceptable:** OOS Sharpe somewhat below IS (trend has been weak recently);
somewhat larger drawdowns; same equity-curve *shape*.
**Not acceptable:** OOS Sharpe ≤ ~0; a drawdown materially larger than anything
IS; qualitatively different shape.

Pre-committed interpretation (decide before looking):

| IS | OOS | Read | Action |
|---|---|---|---|
| 0.7 | 0.3 | Real, regime-degraded | Proceed cautiously |
| 0.7 | 0.0 | Ambiguous | Pause; consider extending OOS before deciding |
| 0.7 | −0.2 | Not what was thought | **Kill** |

If OOS fails: do not tweak and re-test. Kill, or document why OOS is
uninformative and acquire fresh data.

---

## Step 7 — Paper Trading

**3 months minimum, 6 ideal.** Tests live fills vs backtest assumptions,
real-time signal parity, execution edge cases (rolls, holidays, partial fills,
contract changes), and whether a paper drawdown can be held through. Does NOT
validate fast-market fills, specific weekend-gap slippage, or real-money
psychology.

---

## Step 8 — Pre-Live Checklist

Close the `RISK.md` gaps before any live capital:

- [ ] System-wide per-order size limit in `orders/order_handler.py`
- [ ] Kill switch (flag checked in `place_order()`), tested
- [ ] Daily loss limit (realized + unrealized vs configured limit)
- [ ] Max portfolio exposure / margin headroom check
- [ ] Stale-data / disconnect guard before submitting
- [ ] Reconnect logic for IBKR socket drops (1100/1102)
- [ ] Drawdown-budget monitor (alert on approaching the kill threshold) — replaces the old PDT-proximity alert
- [ ] Alerting on rejections, drops, unexpected position state
- [ ] Runbook for "something is wrong"
- [ ] Live account + port (`7496`) set and **verified twice**

---

## Step 9 — Live, Small → Scale

Smallest sizing that exercises the full system end-to-end. Scale only after: a
live drawdown held through; live fills match paper statistically; one weekend
run unattended without incident; no surprises in commissions/margin.

---

## Expected Performance, Capacity, Signal Decay (from published work)

- **Sharpe:** diversified TSMOM ran ~1.0 gross in early-sample studies (e.g.
  Moskowitz–Ooi–Pedersen 2012, ~58 instruments). Realistic *forward* expectation
  is lower — roughly **0.4–0.7 net** over a full cycle that includes the lean
  2011–2019 decade. Do not anchor on 1.0; that is in-/early-sample.
- **Drawdowns:** 20–30% peak-to-trough is normal; drawdowns can last **2–4 years**
  (the 2010s grind). Holding through is the whole game and the reason the premium
  survives.
- **Capacity:** very high for liquid futures; $5M is immaterial. The only
  capacity concern is liquidity in the thinner contracts, not the core.
- **Decay/turnover:** slow signal (multi-month), low turnover (weekly/monthly
  rebalance) — not a fast-decay/crowding-sensitive edge in the HFT sense. The
  crowding risk for trend is multi-decade premium compression, not intraday.

---

## Ways This Backtest Will Lie To Me

1. **Continuous-contract construction.** Back-adjustment method changes returns,
   badly for high-roll-yield contracts (crude); adjusted prices can go negative;
   roll yield can leak into signals. Ratio-adjust for signals, unadjusted for PnL.
2. **Hindsight instrument selection.** Picking the basket you know trended well
   (crude/rates in given decades). Pre-commit the universe on economic span.
3. **Vol-target lookahead.** Using full-sample vol to size. Use PIT trailing vol.
4. **Fractional contracts.** Modeling non-integer sizing the live account can't hold.
5. **Under-modeled roll cost / thin-contract slippage.**
6. **Sharpe flattering** a strategy with long shallow drawdowns and fat
   single-instrument left tails. Look at drawdown duration and tails, not just Sharpe.
7. **Rebalance leakage.** Friday-close signal executed Friday close = lookahead;
   execute Monday open.
8. **Correlation that spikes to 1 in crises** — diversification measured in calm
   periods overstates the 2008/2020 "everything correlates" reality.

---

## Open Decisions

| # | Decision | Recommendation | Status |
|---|---|---|---|
| 1 | Continuous-roll methodology | RESOLVED 2026-06-19: two series per instrument — ratio (proportional) back-adjusted for signals; actual-contract PnL with explicit rolls. Deterministic calendar roll. Full spec in DATA_MODEL.md. | Resolved |
| 2 | v1 instrument list + full-size vs micro | RESOLVED 2026-06-19: ES, ZN, 6E, 6J, 6A, CL, GC, HG — 8, all full-size. One equity + one rates leg (kills within-sector double-counting; inverse-vol ≠ equal-risk under high correlation). FX + commodity carry diversification. Dropped NQ/RTY/ZF/ZB (redundant), NG (idiosyncratic vol/roll/tail). Micros (MES/MGC) only as conditional fallback if vol target later drops to 8%. | Resolved |
| 3 | Futures history data source | RESOLVED 2026-06-20: Databento GLBX.MDP3.
IBKR probe failed (2.5–8y depth). Norgate considered (depth to ~1980) but Databento
chosen for Python-native API, settlement via statistics schema, and Stage-2 minute
reuse. TRADEOFF ACCEPTED: GLBX history begins 2010-06-06, so IS start moves from
2005 to 2011 — forgoing ~6 years of sample power and the 2008 crisis-convexity
datapoint. Mitigation: 2020 (COVID) and 2022 (inflation/rates) remain IN-sample as
stress regimes; regime-segmented evaluation is now mandatory (see Step 3). Pull
INDIVIDUAL contracts via parent symbology (e.g. ES.FUT), NOT vendor continuous;
cache to SQLite; build both #1 series in-house. | Resolved |
| 4 | Capital as single-source config parameter set to $5M | RESOLVED 2026-06-19: `BacktestConfig.starting_cash` default = $5M; `RISK.md` reframed from PDT floor to drawdown budget. | Resolved |
| 5 | Vol target + per-instrument risk cap | RESOLVED 2026-06-19: 10% target portfolio vol (kill-gate headroom); inverse-vol from PIT 60d realized vol scaled to ex-ante 10% under zero-correlation assumption; per-instrument risk cap 25%; vol-estimate floor to prevent inverse-vol explosion. These are the Step-4 sweep centers. | Resolved |
| 6 | Pre-committed OOS interpretation rule | Step 6 table; commit before research | Open |
| 7 | Production multi-symbol engine scope + timing | Gated on Steps 2–5 passing | Deferred |
| 8 | Live kill criteria recast off PDT floor onto drawdown budget | Step 0 table | Open |

---

## Progress Log

| Date | Step | Notes |
|---|---|---|
| 2026-06-19 | 0 | Resumed from parked notes. Capital rebased to $5M; PDT framing removed; universe expanded to include rates/FX; clarified Steps 2-5 run vectorized in notebook (no production engine), engine build gated on Steps 2-5. |
| 2026-06-19 | 1 (data probe) | IBKR data depth probed for all 8 instruments. CONTFUT daily: ES from 2022-03 (1065 bars), ZN from 2023-12 (625), 6E/6J/6A from 2019-09 (~1718), CL from 2018-01 (2084), GC from 2018-06 (1977), HG from 2019-07 (1716). Individual expired FUT from 2005: all 8 return error 200 "no security definition." IBKR covers at most ~8 years, not the required 20+. **Blocked on Open Decision #3 (vendor).** |
| 2026-06-21 | 3 (IS backtest) [file: step4_backtest.py] | **SCREEN PASSED (placeholder gate).** `step4_backtest.py`. 2011-2019 base-case NET Sharpe **0.481** ≥ 0.30 — but 0.30 is the screening placeholder, NOT the committed gate (see 2026-06-21 gate-diagnostics entry for the real beats-best-single test). Gross 0.506; cost drag only 0.025. Full-IS net Sharpe 0.637, +$4.81M on $5M, worst DD −23.89%. 2020/2022 stress: Sharpe 1.90 / 1.33 (crisis convexity confirmed). Findings to carry forward: (a) realized vol runs ~13% vs 10% target (1.1–1.65× every year) — zero-corr ex-ante scaling + 60d-vol lag bias; (b) worst IS DD −23.89% sits **at the 25% OOS kill line and past the −20% live floor** at *realized* (not target) vol — headroom is thin; (c) risk well-diversified (max single-instrument risk share 14.7%, all 10–15%), return more concentrated (6J 25.7%, ZN 19.4%, HG 17%); (d) ZN/CL signal corr −0.686 confirmed but they share sign only 15.5% of IS days (diversifying), rising to 51% inside the worst drawdown (diversification thins in the drought). Signal-lag hand-check verified to the cent. DATA REFINEMENT: collapsed ~51 Sunday pseudo-settlements/yr into the next trading day (clean ~253 obs/yr; makes 252-day = 12mo and sqrt(252) annualization correct) — this was later moved into the canonical build layer (see gate-diagnostics entry). Next: Step 4 (parameter sensitivity), but gated on re-reading the real gate first. |
| 2026-06-21 | 1-2 (data load/build) [file: build_continuous.py] | Databento .dbn.zst files loaded (5022 definition + 5017 statistics files, 2010-06-06 to 2026-06-18). Continuous contracts built for all 8 instruments: ratio-adjusted signal series + actual-contract PnL series. 1,480 contracts, 1.13M settlement records parsed. Deterministic roll calendar: ES/ZN/6E/6J/6A quarterly (64-65 rolls), CL monthly (191 rolls), GC bimonthly (96 rolls), HG monthly (192 rolls). CL had 49 dates with missing front-contract settlement (near-expiry gaps). All validations passed: no negative signal prices, return continuity at rolls verified, anchor invariance within machine epsilon (~3e-16). IS: 2011-07-01 to 2022-12-31, OOS quarantined. Persisted to `data/futures_research.db`. Multipliers hardcoded from CME contract specs (Databento `contract_multiplier` field is INT32_MAX sentinel). ZN quoted in decimal points ($1,000/pt); 6J quoted per single yen (not per 100); HG quoted per pound (not hundredweight). |
| 2026-06-21 | 3 → **KILL** [file: step4_drought_core.py] | **CANDIDATE KILLED as designed — documented negative result.** One confirming computation before the kill (a strict sub-window of the existing IS run via `run_backtest` — NOT a new test, sweep, or OOS): basket NET Sharpe + max DD on the true drought core 2016-2019, plus the 2011-2015 vs 2016-2019 split at the portfolio level. **Result: 2016-2019 drought-core net Sharpe 0.019 (+$49k on $5M), worst DD −21.57%; 2011-2015 secular net Sharpe 0.932 ($2.46M); full base case 0.481 ($2.51M).** 2011-2015 is **98%** of the base-case PnL — the entire gate-window Sharpe is the Abenomics/EZ-bond-bull secular block; the drought core (closest analogue to the hostile 2024-2028 forward regime) earns ≈0 while still breaching the −20% live floor (−21.57%). Kill rationale (see Step 0 "Kill rationale" section): (a) +0.052 diversification margin over best single leg is within noise; (b) basket 0.481 < ~0.58 zero-corr ideal → realized correlation eats the benefit (same root cause as the 13% vol overshoot and −23.89% DD); (c) IS Sharpe concentrated in 2011-2015 secular + 2022; drought-core engines dead (6J standalone −0.26, ZN 0.03 in 2016-2019). **Decision: do NOT start the parameter sweep, do NOT trim the universe on IS Sharpes (hindsight selection + OOS contamination), do NOT open OOS (2023-present stays quarantined).** Code preserved (step4_backtest.py, step4_gate_diagnostics.py, step4_drought_core.py), not deleted. Open Decision: proceed-to-sweep vs reconsider-universe → RESOLVED as KILL. |
| 2026-06-21 | 3 (gate diagnostics) [file: step4_gate_diagnostics.py] | **LOAD-BEARING GATE RUN + secular-artifact test + vol-lag proof + Sunday canonicalization.** (1) **Real gate (beats-best-single):** basket 2011-2019 net Sharpe **0.481** vs best single instrument **6J 0.429** → PASS, but by only **+0.052**. Standalone net Sharpes (2011-2019): 6J 0.429, ES 0.287, ZN 0.280, GC 0.190, CL 0.182, HG 0.178, 6A 0.081, 6E 0.023. The 8-instrument basket adds only ~0.05 Sharpe over just holding 6J. (2) **Secular-artifact test (CONFIRMS the Step 3 worry):** 6J earns 55% of its IS contribution in 2013-2015 Abenomics (standalone Sharpe 1.32) + 28% in 2022 BoJ (1.70), and *loses* money in the 2016-2019 drought core (−12% contribution, standalone Sharpe **−0.26**). ZN earns 38% in 2011-2012 (EZ-crisis bond bull, 1.39) + 39% in 2022 rate hikes (1.98), and is flat in the drought core (Sharpe **0.03**). Both top return legs ≈0 in the 2016-2019 regime — the one most analogous to the expected hostile 2024-2028 forward regime. The IS Sharpe leans on non-repeatable secular episodes. (3) **Vol-PIT lag:** demonstrated (not asserted) on ES hand-check date 2012-07-10 — both the 252d signal window and the 60d realized-vol window end exactly at T (last obs == T), floor not binding, position +16 = `positions.shift(2)`; no vol-pit lookahead. (4) **Sunday collapse canonicalized:** moved out of `step4_backtest.load_clean` (inline) into `build_continuous.collapse_sundays` and baked into `futures_research.db` via `build_continuous.py --recollapse` (preserves blessed pre-collapse settlements; no reparse). The 2 rolls that landed on Sundays (ES, CL) are correctly relocated; is_roll recomputed from active_symbol changes. step4 numbers byte-identical post-migration (0.481 / full-IS 0.637 / DD −23.89%); load_clean now asserts the DB is Sunday-free. CL's 49 missing front-settlement dates are dropped via the `has_settle` filter (next retained date's PnL spans the gap) — confirmed benign. Stale pre-clean ZN/CL signal corr −0.60 is gone: clean DB gives −0.683 and nothing downstream references −0.60. |

### Step 2 known issues

- `futures_contracts` PK is `(raw_symbol)` but should be `(raw_symbol, expiration)`.
  CME single-digit year codes cycle every decade (ESM0 = Jun 2010 AND Jun 2020), so
  `raw_symbol` alone is not unique across decades. Currently harmless: the table is
  rebuilt via `to_sql(if_exists="replace")` from a correctly deduplicated DataFrame
  keyed by `(raw_symbol, expiration)`. Would break under any future incremental-insert
  persist path. Not fixing now; flagging so it's not silently inherited.

### Step 2 output reference (GC signal + PnL, head/tail IS)

**Signal series (GC, IS head):**
```
trade_date  active  raw_settle  signal_price
2011-07-01   GCQ1      1482.6    2130.0967
2011-07-03   GCQ1      1482.6    2130.0967
2011-07-05   GCQ1      1512.7    2173.3423
2011-07-06   GCQ1      1529.2    2197.0483
2011-07-07   GCQ1      1530.6    2199.0598
```

**Signal series (GC, IS tail):**
```
trade_date  active  raw_settle  signal_price
2022-12-25   GCG3      1804.2    2181.6453
2022-12-27   GCG3      1823.1    2204.4992
2022-12-28   GCG3      1815.8    2195.6720
2022-12-29   GCG3      1826.0    2208.0059
2022-12-30   GCG3      1826.2    2208.2477
```

**PnL series (GC, IS head, $/contract):**
```
trade_date  active  settle   pnl_daily
2011-07-01   GCQ1   1482.6   -2020.00
2011-07-03   GCQ1   1482.6       0.00
2011-07-05   GCQ1   1512.7    3010.00
2011-07-06   GCQ1   1529.2    1650.00
2011-07-07   GCQ1   1530.6     140.00
```

**PnL series (GC, IS tail, $/contract):**
```
trade_date  active  settle   pnl_daily
2022-12-25   GCG3   1804.2       0.00
2022-12-27   GCG3   1823.1    1890.00
2022-12-28   GCG3   1815.8    -730.00   (1815.8-1823.1=-7.3 * $100)
2022-12-29   GCG3   1826.0    1020.00
2022-12-30   GCG3   1826.2      20.00
```

---

## Handoff note for Claude Code

Two separate tasks, in order:

1. **Capital reframe (do first — see the dedicated prompt).** Consolidate the
   account/base-capital assumption to a single config source of truth and set it
   to $5,000,000. Rewrite the PDT-floor risk *framing* in `RISK.md` onto a
   drawdown budget. Do **not** blanket-change strategy-specific notionals like
   `spy_short_reversal`'s `target_notional`. Report every reference found and what
   was changed vs left alone.

2. **Stage 0 vectorized notebook (only after universe, signal spec, and kill
   gates are locked here on Desktop).** Portfolio-level trend backtest in
   `research/diversified_trend/`, integer-rounded contracts, PIT vol, roll +
   cost model. No production-engine changes. This is the cheap kill gate for the
   whole thesis.

Do not build the production multi-symbol engine until Steps 2–5 pass.
