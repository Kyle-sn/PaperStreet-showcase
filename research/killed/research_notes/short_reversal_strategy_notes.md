# Research Workflow — SPY Short-Horizon Reversal

| | |
|---|---|
| **Strategy name (registry key)** | `spy_short_reversal` (provisional) |
| **Family** | Bar (`BaseStrategy`) — **net-new code (RSI-based)**; does **not** reuse `MeanReversionStrategy` (see §3, verification note) |
| **Status** | **PARKED** — §7 OOS one-shot **FAILED** the binding gate (does not beat the 200-day timing-only baseline OOS; crowding decay per §1). Workflow complete; no §8/§9. |
| **Instrument** | SPY (primary); QQQ deferred (see §2) |
| **Horizon** | Days-scale; 1–5 day holds (overnight, not intraday) |
| **Doc location** | `research/research_notes/short_reversal_strategy_notes.md` (authoritative) |
| **Related docs** | `STRATEGY.md`, `BACKTESTING.md`, `RISK.md`, `strategy/README.md` |

This doc follows the standard 9-step research template, **prepended with a Step 0** that
establishes a clean research scaffold (see §0). It is a plan, not a result — every number below
is a hypothesis to be tested or a decision to be made, not a finding. Update the Open Decisions
list as decisions are made; treat resolved decisions as settled.

> **Verification findings folded in (post-§3).** A Claude Code verification pass corrected
> several assumptions baked into earlier drafts: (1) `research/session.py` is load-bearing for the
> live system, **not** stale — see §0; (2) the data is now **single-source IBKR**, so the
> yfinance/IBKR splice concern is moot — see §2; (3) the existing `MeanReversionStrategy` does
> **not** implement the §3 "Option B" as described, so any implementation here is net-new — see §3;
> (4) the IBKR fetch path hardcodes `whatToShow="TRADES"` while research validated on
> `ADJUSTED_LAST` — a **parity flag** resolved in §2 / Decision #2. Findings summary in §3.

> **Scope discipline.** This is a single-symbol, time-series reversion candidate. It is **not**
> cross-sectional reversal, **not** trend following, **not** intraday. Those framings were
> considered and rejected for this candidate — see §1 and the Decided-Against notes in
> `ROADMAP.md`. Do not silently re-introduce them.

---

## 0. Research Environment (build fresh — engineering setup, not research)

**Assume there is no usable research environment.** The legacy `research/explore.ipynb` and its
Random-Forest / parameter-optimization pipeline are **stale scaffolding from earlier work and are
not to be trusted, extended, or imported.** Start clean for this candidate.

> **Exception (verification finding): `research/session.py` is NOT stale.** It is load-bearing
> for the live system — imported by `run_live.py`, `backtesting/data.py`, and
> `market_data/test_market_data.py`. Reuse it as the sanctioned connect path; do **not** avoid or
> reimplement it. "Treat `research/` as stale" applies only to the legacy notebook/RF pipeline,
> not to `session.py`.

Rules for the scaffold:

- **Build a fresh, candidate-specific notebook** — `research/spy_short_reversal/notebook.ipynb` (built;
  self-contained, executed, cache-first so it re-runs offline). Do not graft onto the old notebook.
- **Leave the old files in place.** Do not delete legacy files — that's a separate cleanup
  decision (scratch builders created during §3 were removed; the legacy pipeline was left untouched).
- **No ML, no grid search at this stage.** §3 is *descriptive* signal characterization. A
  classifier on ~5,000 daily rows is an overfitting hazard, not a measurement tool. A signal
  filter (ML or otherwise) is a much-later question and only earns consideration if the raw edge
  is real. Resist the kitchen sink.
- **Reuse production code where it exists** (the cache-first bar loader, the
  `MeanReversionStrategy` mechanism) rather than reimplementing it in the notebook — that keeps
  research close to what the live system actually does. Confirm what's actually there before
  reusing it (see the Claude Code handoff).

This does not change the 9-step order; it precedes it. §3's notebook work now means *the fresh
notebook*, not the legacy one.

---

## 1. Framing

### Economic rationale

Equity indices overreact to short-term shocks (macro prints, liquidity demand, forced flow) and
partially revert over the following days. We harvest that reversion **long-only**: buy
short-term weakness, exit on the bounce. Two tailwinds stack here:

1. **The reversion itself** — documented short-horizon negative autocorrelation in index returns.
   (Caveat: at the *index* level this is weaker and more regime-dependent than the single-stock
   cross-sectional effect; daily index autocorrelation has flipped positive in some sub-periods.
   The edge is real but thin and not constant — see Expected signature.)
2. **The equity risk premium** — because the underlying drifts up over time, buying dips is the
   *correct side* of the trade. Fading rallies (the short side) fights the drift and is strictly
   worse, which is why long-only is an advantage here, not just an architecture constraint.

### What this candidate is and is not

- **Is:** a time-series effect on a single instrument → in scope for the single-symbol architecture.
- **Is not:** the cross-sectional short-term reversal effect (long relative losers / short relative
  winners), which is the *stronger* documented version but requires multi-symbol infrastructure
  that does not exist (`ROADMAP.md` → multi-symbol backtester). We are deliberately trading the
  weaker, in-scope version.

### Why this candidate over the alternatives

- **vs. trend following:** equity reversion lives at the **days** horizon; equity trend does not
  switch on until **months** (3–12m lookbacks), past this candidate's hold cap. At days-to-weeks
  the index is near a random walk. Trading trend here means trading an effect that isn't present.
- **vs. single-name reversion:** a single stock can gap on earnings, halt, or blow up overnight —
  a dip-buyer walks into it. With a $25k PDT floor where drawdown into the floor is a **system
  failure, not a P&L event**, removing idiosyncratic tail risk by trading the index is decisive.

### A known-strategy warning (Option A specifically)

The RSI(2) variant (Option A, §3) is the **Connors/Alvarez RSI(2) system** almost verbatim —
one of the most publicly documented retail strategies of the last 15 years. That is not
disqualifying, but it means the flattering backtests everyone cites are **pre-publication**
(roughly pre-2009). Treat in-sample results on old data with extra suspicion, and weight the
OOS toward **post-2015** specifically, where a decade of crowding has had time to erode the
published edge (see §7).

### Expected signature (hypotheses, to be confirmed)

| Property | Expectation | Note |
|---|---|---|
| Signal decay | 1–5 days | Cost-tolerant relative to intraday MR, but still high-ish turnover |
| Win rate | 65–75% | High hit rate — matches the "right most of the time" preference *honestly* this time |
| Skew | **Negative** | Win small often, lose large in regime changes. This is the core *catastrophic* risk. |
| Net Sharpe | 0.3–0.6 good regime, ~0 bad | Degraded and crowded post-2018; older backtests flatter |
| Max drawdown | Clusters in crashes | The strategy buys *into* falling markets unless gated (see trend filter) |
| Capacity | Irrelevant at $50k | SPY is effectively bottomless |

### The two distinct risks — keep them separate

1. **Catastrophic risk (negative skew):** buying dips bleeds badly when a "dip" is the front of a
   regime change (2008, 2022's failed-bounce grind). **Mandatory mitigant: a long-term trend
   filter** — only take long-reversion signals when the index is above its long MA (200-day
   default), stand aside below it. Economically motivated (don't provide liquidity into a regime
   change), not a fitted knob, and the difference between sidestepping the worst regimes and
   catching falling knives through them.

2. **The actually-likely failure (mediocrity):** for an **unlevered single-SPY** position, the
   path to the $25k floor requires roughly a −50% drawdown *while fully invested* — which is
   exactly what the trend filter exists to prevent. So the realistic way this candidate dies is
   not blowing through PDT; it's **sitting in cash much of the time and failing to beat
   buy-and-hold on a risk-adjusted basis.** The floor is the backstop; the benchmark is the bar.
   Weight evaluation accordingly (§4, §7).

---

## 2. Universe & Data

| Item | Decision / plan |
|---|---|
| Primary instrument | SPY |
| Second instance | QQQ — **deferred**. ~0.9 correlated to SPY, so it is *not* diversification. Add only after SPY validates standalone, eyes open that the book is still essentially one beta-reversion bet. |
| Bar size | `1 day` |
| `whatToShow` | **`ADJUSTED_LAST`** (total-return) — **resolved**, see Decision #2 and the parity note below |
| Data source | **Single-source IBKR** (verification finding). The local cache held only 64 sparse, non-contiguous bars (unusable) and `yfinance` has no network in the environment — so the splice never happened. Daily SPY 1996→2026 pulled from IBKR and stored at `research/data/spy_daily_{trades,adjusted}.csv` for offline reproducibility. |
| History needed | Enough for (a) 200-day filter warmup + signal lookback, and (b) multiple regimes across IS and OOS. Full available IBKR history pulled (1996→2026). |
| Survivorship bias | None — single ETF. One classic bias removed for free. |

> **Parity flag (verification finding) — RESOLVED.** The IBKR fetch path hardcodes
> `whatToShow="TRADES"` (`ibkr_client.py:100`), but research validated the signal on
> `ADJUSTED_LAST`. TRADES is **split-only** (price-return); ADJUSTED_LAST is **total-return**
> (ratio 1.45→1.00 since 1996). Decision: standardize on **`ADJUSTED_LAST` everywhere** —
> research, backtest, and live — by making `whatToShow` configurable end-to-end (default stays
> `TRADES` to preserve existing behavior; this candidate sets `ADJUSTED_LAST`). Rationale: the
> *signal* is nearly indifferent (RSI(2)/SMAs are return-based; SPY's dividend ≈ 0.03% over a
> 5-day hold), but the *equity curve* is not — price-return would understate the strategy **and**
> the buy-and-hold/timing benchmarks by the whole dividend stream and make the §4 comparison
> meaningless. Live fills at raw price + dividends-as-cash sum to the same total return the
> adjusted backtest shows, so live/backtest stay consistent. **This change touches shared infra —
> make it additive, default-preserving, and tested.**

### Data hygiene

- **Dividend / split adjustment — RESOLVED.** Use `ADJUSTED_LAST` (total-return) consistently
  across signal and PnL, IS and OOS (see the parity note above and Decision #2). TRADES is
  split-only; ADJUSTED_LAST is total-return.
- **Data-source splice seam — MOOT.** Considered when deep history was expected to come from
  `yfinance` spliced with IBKR. Single-source IBKR (above) removes the seam entirely; no join,
  no discontinuity to police. Kept here only as the record of why it's no longer a concern.
- **Timezone / session.** Daily bars are date-only; confirm the `bar_datetime` normalization in
  `database/market_data.py` handles these cleanly (it should — daily → `"YYYY-MM-DD"`).

---

## 3. Signal in the Notebook (fresh `research/spy_short_reversal/notebook.ipynb`)

Characterize the **signal** before building the strategy wrapper, in the fresh notebook from §0
(not the legacy pipeline). Per the signal-vs-strategy distinction: for mean reversion the signal
is only legible through a minimal wrapper, but the goal at this stage is to *measure the edge*,
not tune a strategy. **Descriptive only — no ML, no grid search.** Resist tuning here.

### Candidate signal specs (lead with one, characterize both)

**Option A — RSI(2)-style (canonical, well-documented — and heavily mined, see §1):**
- Entry: `RSI(2) < threshold` (start 5–10) **and** `close > SMA(200)`.
- Exit: `close > SMA(5)` or RSI normalizes.

**Option B — deviation/z-score:**
- Entry: `z = (close - SMA(N)) / std(N) < -k` (start `N ∈ [5,10]`, `k ∈ [1,2]`) **and** `close > SMA(200)`.
- Exit: `z >= 0`.
- **Fix the window.** The current live config `window=3` is microstructure-timescale noise and
  will not survive costs — `N ≥ 5` is the floor for this to be a days-scale reversion signal.
- **Verification finding — this is NOT a clean reuse of `MeanReversionStrategy`.** That class
  computes `(close − SMA)/std` with *population* std (`indicators.py:53`), sells on the *opposite*
  deviation (no `z >= 0` exit), and has **no trend filter**. The §3 Option B exit + 200-day gate
  would be **new code**, not a wiring-up of the existing class. (Moot for now — Option A is chosen,
  see outcome below — but corrects the earlier "reuses the mechanism" framing.)

The 200-day trend-filter gate applies to **both** options and is not optional (see §1).

### What to measure (descriptive, not optimized)

- Conditional forward return after the signal vs. unconditional baseline (`FORWARD_BARS` ≈ 3–5).
- The **decay curve**: how the edge fades over forward bars 1…N — confirms the days-scale hold.
- Hit rate and the loss distribution (look for the negative tail — it must be visible).
- Behavior of the signal **with vs. without** the trend filter, specifically through a downturn.
- **Edge over the timing filter alone (critical attribution check):** also measure the
  **"flat below 200-day" timing-only baseline** — buy-and-hold SPY while `close > SMA(200)`,
  in cash otherwise, with *zero reversion signal*. The reversion edge's marginal value is
  `(RSI/z + filter)` minus `(filter only)`. A long-only strategy that goes flat below the 200-day
  is mechanically a **market-timing overlay with a reversion entry bolted on** — most of the
  Sharpe lift vs. buy-and-hold comes from the *timing*, not the *reversion*. If the reversion
  signal can't beat naive 200-day timing, you've built a 200-day SMA timer with extra steps and
  the candidate should be parked.

> Use `shuffle=False` for any train/test work (lookahead trap). Do not touch the OOS window here.

### §3 outcome (in-sample 1996–2014, descriptive, ADJUSTED_LAST basis) — GATE: GO

- **Decision #2 resolved:** TRADES = split-only, ADJUSTED_LAST = total-return → use ADJUSTED_LAST.
- **Signal beats the binding baseline.** Option A (RSI(2)<10 + 200-day filter): fwd-5d **+0.77%**,
  hit **63.8%**, n=229, vs timing-only **+0.21%** → **marginal +0.56%/5d over the 200-day timer**,
  not merely over buy-and-hold.
- **Option A > Option B** (z5<−1: +0.25% marginal) → **Decision #1 → Option A.**
- **Days-scale confirmed:** marginal edge builds to a peak at h≈4–5, then flattens.
- **Negative tail real:** even filtered, worst-1% −6.5%, worst −12.8%, skew −0.88 — the predicted
  signature, present and visible.
- **Filter works through a downturn:** GFC signals cut 52 → 2 (stood aside through the crash).
- **Timing baseline is already strong:** Sharpe 0.52 / maxDD −29% vs B&H 0.45 / −55%. Most of the
  risk-adjusted lift is the *timing*; reversion adds conditional alpha on top. The §4 bar is
  beating **0.52 Sharpe**, net of costs — that is still unproven.
- **Caveat (load-bearing for §4):** these are **gross close-to-close** returns. Live/backtest fills
  are **next-open**, and commission ($1 min) + slippage are not yet applied. The realized net edge
  in §4 **should come in below +0.56%** — that direction is healthy; a realized edge ≥ the gross
  number is a red flag for a lookahead leak.

---

## 4. In-Sample Backtest

Run via `run_backtest(BacktestConfig(...))` once the signal is implemented as a strategy class
(implementation = Claude Code). The engine's `next_open` fill model enforces no same-bar
lookahead by design (`BACKTESTING.md`).

> **§4 prerequisites (from §3 findings):** (1) `spy_short_reversal` is **net-new code** (RSI-based)
> built against the real `BaseStrategy`/registry/`OrderRequest` interfaces — **not** a subclass or
> reuse of `MeanReversionStrategy`. (2) The **parity fix must land first**: make `whatToShow`
> configurable end-to-end and run this backtest on `ADJUSTED_LAST` so it matches §3 and the live
> path (see §2 parity note). (3) **In-sample only** — `<= 2014-12-31`; do not load 2015+.

```python
# illustrative config — not implementation
CONFIG = BacktestConfig(
    strategy_name="spy_short_reversal", symbol="SPY",
    strategy_params={...},          # the chosen signal spec + trend_filter=200
    bar_size="1 day", starting_cash=50_000,
    slippage_bps=...,               # see §6
)
```

### Two mandatory benchmarks

A long-only equity strategy that is in the market only part of the time **must justify itself
against doing something simpler.** Compare against **both**:

1. **Buy-and-hold SPY.** It will almost certainly have *lower raw return* (it sits in cash
   often); the case for it is **risk-adjusted** — higher Sharpe and/or materially lower drawdown
   because it is flat during the worst stretches (trend filter pulls to cash below the 200-day).
2. **Timing-only baseline ("flat below 200-day").** The §3 control: buy-and-hold while
   `close > SMA(200)`, cash otherwise, no reversion. **This is the harder bar.** If
   `RSI/z + filter` doesn't beat timing-only on the committed risk-adjusted metric, the reversion
   signal is adding nothing and the candidate is parked. Beating buy-and-hold but *not* the
   timing baseline means you've validated a 200-day SMA timer, not a reversion edge.

Commit the exact comparison metric in Open Decisions before looking.

### Checks at this stage

- Full metrics table (`compute_metrics`): Sharpe, max DD, win rate, avg win/loss, trades, commission.
- **Trade count** — enough for statistical power (days-scale reversion should give plenty; if it
  doesn't, the signal is too rare to validate).
- Drawdown timing — confirm DDs cluster where expected (downturns) and that the filter mutes them.

### §4 outcome (in-sample 1996–2014, ADJUSTED_LAST, $50k, next-open + cost) — PASS

Implemented as `strategy/spy_short_reversal.py` (net-new, RSI-based; `WilderRSI` in
`strategy/indicators.py` is validated bit-for-bit against the §3 notebook RSI — max abs diff 0.0,
n=229 matches). Backtest is reproducible offline: `research/spy_short_reversal/is_backtest.py`
seeds **IS-only** ADJUSTED_LAST bars into the cache (OOS literally absent) and runs the three-way
comparison through `run_backtest`. Parity fix (configurable `whatToShow`, default `TRADES`) landed
first — see Decision #2/#12 and `IBKR_NOTES.md`.

**Frozen spec (Decision #4 resolved → single short-MA-cross exit):** Entry `RSI(2)<10 & close>SMA(200)`;
exit `close>SMA(5)`; single entry (enter only when flat, exit sells the full position); long-only.

| Strategy (IS, $50k, next-open, comm $0.005/sh $1-min, slip 1bp) | Sharpe | maxDD | totRet | annRet | win% | trades | round | comm |
|---|---|---|---|---|---|---|---|---|
| **spy_short_reversal** | **0.78** | **−8.5%** | 92.5% | 3.61% | 76.3% | 262 | 131 | $737 |
| buy_and_hold | 0.45 | −55.5% | 268.8% | 7.31% | — | 1 | — | $5 |
| timing_sma (binding bar) | 0.50 | −23.1% | 122.5% | 4.42% | 27.0% | 127 | 63 | $360 |

- **Beats the binding timing-only bar** (Sharpe 0.78 vs 0.50, and vs §3's gross 0.52) **and**
  buy-and-hold (0.45) on the committed risk-adjusted metric — and crushes both on drawdown
  (−8.5% vs −23%/−55%), as expected for a strategy in cash ~95% of the time. **Honest caveat:**
  it wins on *risk-adjusted* terms, **not** absolute return — annualized 3.61% trails both
  benchmarks because it sits in cash, and fixed-notional (non-compounding) sizing further caps it
  vs compounding B&H. Sharpe/maxDD is the fair, cost-equal comparison (Decision #8).
- **Cost stress (§6, double comm + slip):** Sharpe 0.78 → 0.75, totRet 92.5% → 88.4%, still beats
  timing-only and stays positive — **edge survives.**
- **Commission drag (Decision #6):** full-notional single entry → ~0.56 bps/fill; the **$1 minimum
  never binds** (0/262 fills floored). A 10-share sizing would have paid the floor and bled ~10bp/side.
- **Acceptance #1 (next-open):** `config.fill="next_open"`; engine fills bar-N's signal at bar
  N+1's open before generating N+1's signal (no same-bar lookahead). Pinned by
  `tests/test_backtest.py::test_next_open_fills_at_next_bar_open_not_signal_bar_close`.
- **Acceptance #2 (no lookahead leak):** realized next-open, post-cost 5d marginal over timing-only
  = **+0.43%**, *below* §3's gross close-to-close **+0.56%** (gross reproduced exactly). Direction
  is healthy — a realized ≥ gross would have flagged a leak.

**Gate: PASS** — proceed to §5 (parameter sensitivity), then the §7 OOS one-shot. OOS (2015+)
remains untouched.

---

## 5. Parameter Sensitivity

Sweep the knobs and look for **plateaus, not spikes.** A real edge is robust to small parameter
changes; an edge that exists only at one combination is overfit.

- Signal: window `N` / RSI threshold; entry `k`; exit rule.
- Filter: trend-filter length (200 default; test 100 / 150 for robustness, not to optimize).
- Tooling: loop `run_backtest()` over a grid. The dedicated grid-search wrapper is a `ROADMAP.md`
  backlog item; until built, a thin loop is fine (it honors the same inventory-aware fills —
  this is exactly why we don't reach for a vectorized sweeper; see `ROADMAP.md` → Decided Against).
- **Red flag:** PnL that's strong at `N=7` and zero at `N=6` and `N=8`. Park it if so.

### §5 outcome (in-sample 1996–2014, ADJUSTED_LAST, $50k) — frozen point = PLATEAU

Reproducible offline via `research/spy_short_reversal/sensitivity.py` (IS-only cache seed
reused from §4; OOS literally absent). The frozen spec is **unchanged** — §5 tests robustness,
it does **not** re-pick the spec, even where the sweep surfaced a higher-IS-Sharpe cell.

**Task 0 — cash / risk-free treatment (resolved the dominant-lever question).**
Confirmed actual state: `backtesting/metrics._sharpe` used **rf = 0** and `Portfolio.mark` holds
idle cash at **0%** — a *self-consistent* pair ("excess over cash" where cash = 0%), **not a bug**.
Made Sharpe genuinely rf-aware additively (`compute_metrics(..., risk_free_rate=0.0)`, default
preserved, unit-tested; all 137 tests green). Recomputed the §4 three-way table two ways,
consistently across all three legs (idle-cash reconstruction validated to <1e-6 against the engine
curve at 0%):

| | Sharpe | maxDD | totRet | annRet | %cash |
|---|---|---|---|---|---|
| **(a) idle cash @ 0% (current)** | | | | | |
| spy_short_reversal | **0.78** | −8.5% | 92.5% | 3.61% | 90.6% |
| timing_sma | 0.50 | −23.1% | 122.5% | 4.42% | 32.9% |
| buy_and_hold | 0.45 | −55.5% | 268.8% | 7.31% | 0.0% |
| **(b) idle cash @ 3M T-bill (rf = same series)** | | | | | |
| spy_short_reversal | **0.75** | −6.0% | 165.0% | **5.41%** | 90.6% |
| timing_sma | 0.36 | −17.0% | 156.7% | 5.23% | 32.9% |
| buy_and_hold | 0.33 | −55.5% | 268.7% | 7.31% | 0.0% |

- **Ranking `spy_short_reversal > timing_sma > buy_and_hold` holds both ways.**
- **Sharpe is the *wrong* place to expect a big move; absolute return is the lever.** Crediting
  idle cash lifts the candidate's annRet **+1.80%** (3.61% → 5.41%) because it sits in cash 90.6%
  of the time; B&H barely moves (always invested). Under (b) the candidate's **totRet (165%) now
  exceeds timing-only (157%)** *and* its drawdown is ~3× smaller — so the "wins on risk-adjusted,
  loses on absolute return" caveat from §4 **softens** once idle cash is fairly credited.
- **The candidate's Sharpe lead actually *widens* under the fair convention** (gap to B&H
  0.33→0.42): the rf subtraction is a pure penalty to the fully-invested benchmarks but is offset
  for the candidate by the cash it genuinely earns. The economically-correct excess-over-cash
  convention favors the cash-heavy strategy, not the reverse.
- **Not silently adopted.** Default stays rf=0 / cash@0% pending sign-off (see Decision #13). The
  T-bill series is FRED TB3MS annual averages, hardcoded (no network in-env, per §2).

**Task 1 — sensitivity sweep (one axis at a time, then a 2D grid; Sharpe convention-invariant).**

| axis (frozen) | values → Sharpe | shape |
|---|---|---|
| RSI entry (10) | 5→0.66, **10→0.78**, 15→0.69, 20→0.59 | hump; graceful, no cliff |
| RSI period (2) | **2→0.78**, 3→0.45, 4→0.27 [thin n=9] | steep — but *by signal definition* (see below) |
| Exit SMA (5) | 3→0.58, **5→0.78**, 10→**0.90** | monotonic ↑; IS-best is 10, frozen 5 is *conservative* |
| Trend SMA (200) | 100→0.59, 150→0.60, **200→0.78**, 250→0.67 | peak at 200; 100/150/250 all still > B&H 0.45 |

2D grid (rsi_entry × sma_trend), Sharpe / round-trips:

```
              100        150        200        250
 rsi<5    0.48/45    0.44/57    0.66/63    0.54/69
 rsi<10   0.59/110   0.60/123  *0.78/131*  0.67/139
 rsi<15   0.61/175   0.55/188   0.69/196   0.62/202
 rsi<20   0.67/236   0.51/240   0.59/248   0.53/252
```

**Verdict: PLATEAU with a mild peak at the frozen point — not a SPIKE.**
- Every one of the 16 grid cells is a **positive Sharpe in 0.44–0.78 with adequate trade counts**
  (only rsi<5/sma100 at 45 round-trips approaches thin). The doc's red-flag test — "strong at one
  setting, dead at the adjacent ones → park" — is **not triggered**: neighbors degrade gracefully
  (0.55–0.69), none collapse.
- **Yellow flag, surfaced honestly:** the frozen point (10, 200) is the **single highest cell** of
  the grid. That is the §5 warning sign for an implicitly-tuned spec. Read in context, though, the
  frozen params are the **textbook-canonical Connors RSI(2)<10 / 200-day** values — §3/§4 *adopted*
  them, they were not grid-searched out — so "IS-best coincides with the textbook" is exactly what
  a *real published IS edge* looks like, and exactly why §1 says IS strength proves little for a
  mined strategy and the **post-2015 OOS is the decisive test**. It is a peak-on-a-plateau, not a
  knife-edge.
- **Two axes with real falloff, both benign:** (1) **RSI period** concentrates the edge at 2 — but
  2 *defines* the signal (the short-horizon oversold spike); 3/4 measure a weaker, rarer effect and
  thin the trades to the point of meaninglessness (n=9 at period 4), so this is signal definition,
  not a fitted knob. (2) **Exit SMA** is *monotonically increasing* — the IS-best is exit=10
  (Sharpe 0.90), so freezing at 5 is **conservative**, the opposite of cherry-picking the peak.

**Optional (information-only) hard-stop variant — NOT a spec change.** Frozen + exit on
`close < SMA(200)`: Sharpe 0.78 → 0.71, maxDD −8.5% → −8.7%, totRet 92.5% → 75.1%. The stop
**does not improve IS drawdown** and trims return — because the 200-day *entry* gate already keeps
holds inside uptrends and IS dodged 2008, so the stop mostly cuts winning bounces short. This argues
*against* a stop for the IS regime, but the informative question is OOS (2020/2022 are in OOS, §7) —
flagged for a possible future spec decision, deliberately not adopted now.

**Gate: PLATEAU confirmed → proceed to §7 OOS one-shot.** Frozen spec unchanged. OOS (2015+)
untouched.

---

## 6. Cost Stress

Mean reversion is cost-sensitive — small per-trade edge, high turnover — so costs are
existential, not cosmetic.

- **Commission (IBKR Fixed):** ~$0.005/share, **$1.00 minimum**, 1% max. The **$1 minimum
  dominates at small share counts** — e.g. ~10 shares costs the $1 floor, not $0.05, which on a
  few-thousand-dollar notional is a meaningful bps drag per side and compounds over many round
  trips. **Sizing interacts with cost efficiency**: larger orders amortize the minimum. Model
  this explicitly rather than assuming per-share linear cost.
- **Slippage:** SPY is penny-wide; half-spread ≈ 0.5–1 bp. Use `slippage_bps` ≈ 1 as a
  conservative default for a liquid large-cap (`BACKTESTING.md`).
- **Stress test:** double the commission + slippage assumptions and confirm the edge survives. An
  edge that only exists at optimistic costs is not an edge for a real account crossing the spread.

---

## 7. Out-of-Sample One-Shot

The single most important step. The OOS tail is **never touched** during §§3–6.

> **Split — LOCKED (Decision #3): IS ≤ 2014-12-31, OOS 2015+.** All §3 work was gated to IS on this
> boundary. The OOS (2015→2026) carries 2018, the 2020 COVID crash, and 2022 — so it satisfies both
> requirements below by construction. Run **nothing** on 2015+ until the one-shot.

- **Regime requirement:** the OOS window **must** contain a failed-bounce / downtrend regime —
  satisfied: 2022 (and 2020) are in OOS.
- **Crowding requirement:** because Option A is a heavily-published strategy (§1), the OOS must
  lean on **post-2015** data, where the documented edge has had time to decay — satisfied: OOS is
  entirely post-2015. (The strong IS edge sits partly in the pre-2009 era it was published in, so
  IS strength is near-expected and proves little on its own — the OOS is the real test.)
- **Commit pass/fail criteria *before* looking** (record in Open Decisions). Candidate gates:
  - OOS Sharpe within a stated tolerance of IS Sharpe (no collapse).
  - OOS max drawdown below a floor-implied limit (see §9 sizing).
  - Beats **both** benchmarks on the committed risk-adjusted metric — buy-and-hold SPY **and**
    the timing-only baseline (§4). The second is the one that matters.
  - Trade count sufficient for the result to be meaningful.
  - The trend filter demonstrably mutes the downturn drawdown (validate *through* it, not around it).
- **One shot.** If you look, tune, and re-run, the OOS is burned — that's just a second IS period.
- **Kill criterion:** if the edge collapses OOS, it was overfit to the IS regime. Park the
  candidate; document why. A parked candidate is a valid outcome of this workflow.

### §7 pre-registration — GATES LOCKED before viewing OOS (Decision #7)

Committed **before** any 2015+ backtest is run. All metrics on the **convention-(b)** basis
(idle cash credited at 3M T-bill, rf = same series, excess-over-cash), applied consistently to all
three legs (Decision #13). Reference IS-(b) numbers: candidate **Sharpe 0.75**, **maxDD −6.0%**,
131 round-trips over ~18.5y. OOS window 2015-01-01 → present (~11.5y; ~80 round-trips expected).
Indicators are primed on a pre-2015 warmup slice (load from 2013-06-01) but **metrics and
round-trips are measured on the 2015-01-01+ slice only** — the warmup bars trade but are excluded,
so no IS contamination and the strategy is primed exactly as it would be live.

| # | Gate | Threshold | Rationale |
|---|---|---|---|
| G1 | **Beats both benchmarks on Sharpe** (binding) | OOS candidate Sharpe (b) **> timing_sma AND > buy_and_hold** | Decision #8 committed metric. Beating **timing-only** is the one that matters — else it's a 200-day timer with extra steps. |
| G2 | **No Sharpe collapse** (degradation tolerance) | OOS Sharpe (b) **≥ 0.40** (≥ ~53% of IS-(b) 0.75) | Prevents a degenerate "technically beats two near-zero benchmarks" pass; the edge must stay economically real post-crowding. |
| G3 | **Max-drawdown ceiling** (floor-implied, §9) | OOS maxDD **shallower than −20%** | From $50k that is $40k, comfortably above the $25k PDT floor; cf. IS-(b) −6.0%. Leaves room for OOS to be worse than IS. |
| G4 | **Trade-count sufficiency** | **≥ 40** OOS round-trips | Matches the §5 THIN threshold; below this the Sharpe is not meaningful. |
| G5 | **Trend filter mutes the downturn** (diagnostic, must hold) | OOS maxDD **materially shallower than B&H** over the same window, and entries demonstrably thinned through 2020/2022 | Validate *through* the downturn, not around it (§1 catastrophic-risk mitigant). |

**Verdict rule:** PASS requires **G1–G4 all true AND G5 holds**. Any failure → **FAIL → park and
document why** (a valid outcome). **No re-tuning on OOS regardless of result** — one shot. If
G1 fails specifically (does not beat timing-only), the candidate is a 200-day timer, not a
reversion edge, and is parked on that basis.

### §7 outcome (OOS one-shot, 2015-01-01 → 2026-06-12, ADJUSTED_LAST, $50k) — FAIL → PARK

Run once via `research/spy_short_reversal/oos_backtest.py` (frozen spec unchanged; gates G1–G5
locked **before** this executed; convention (b), all three legs; indicators primed on a 2013-06-01
warmup slice, metrics measured on the 2015+ slice only). **No re-tuning — one shot, honored.**

| Strategy (OOS, convention (b): idle cash @ 3M T-bill, rf = same series) | Sharpe | maxDD | totRet | annRet | round | %cash |
|---|---|---|---|---|---|---|
| **spy_short_reversal** | **0.52** | −7.7% | 68.8% | 4.69% | 99 | 88.5% |
| timing_sma (binding bar) | 0.63 | −14.7% | 132.1% | 7.66% | 31 | 17.5% |
| buy_and_hold | 0.70 | −33.7% | 335.2% | 13.75% | — | 0.0% |

Reference convention (a) (rf=0, cash@0%) tells the **same story**: candidate 0.57 vs timing 0.76 vs
B&H 0.82 — so the verdict is robust to the cash-treatment question (Decision #13 didn't change it).

| Gate | Result | |
|---|---|---|
| **G1 beats both benchmarks on Sharpe** (binding) | cand 0.52 **<** timing 0.63 **<** B&H 0.70 | **FAIL** |
| G2 no Sharpe collapse (≥ 0.40) | 0.52 | pass |
| G3 maxDD shallower than −20% | −7.7% | pass |
| G4 ≥ 40 round-trips | 99 | pass |
| G5 filter mutes the downturn (DD < B&H) | −7.7% vs −33.7%; 2020 entries=8 (Feb–Mar crash=**1**), 2022=2 | pass |

**§7 VERDICT: FAIL (G1) → candidate PARKED.**

**What happened, honestly.** The signal did **not** collapse in absolute terms — OOS Sharpe 0.52,
maxDD −7.7%, 99 round-trips, all healthy. It failed the **one bar that matters**: it does **not beat
the 200-day timing-only baseline** (0.52 < 0.63), and it also trails buy-and-hold. Per the framing
locked since §3/§4, *beating B&H but not timing-only means the timing did the work, not the signal* —
here it beats **neither**, so the reversion entry **subtracted** risk-adjusted value OOS versus simply
holding above the 200-day. The candidate is a 200-day SMA timer with extra steps, and a **worse** one
than the naive timer (the oversold entry keeps it in cash 88.5% of the time and makes it miss upside
the timer captures).

**This is the predicted crowding decay (§1), not a surprise.** The IS edge inverted: IS-(b) the
candidate led 0.75 vs timing 0.36 / B&H 0.33; OOS it trails both. The strong IS result sat largely in
the pre-2009 era the Connors RSI(2) system was published in; post-2015, a decade of crowding has
eroded the marginal reversion edge to nothing — exactly the §1 warning and exactly why the OOS was
weighted post-2015. The §5 "yellow flag" (frozen point = textbook-canonical = IS-best) reads, in
hindsight, as the published-but-decayed edge it warned it might be.

**The trend filter did its job (G5 pass) — which is the point, not a consolation.** Only **1** entry
fired in the Feb–Mar 2020 crash and just 2 across 2022; the candidate's −7.7% maxDD vs B&H's −33.7%
is real and large. But that drawdown control is the **200-day filter**, available far more cheaply via
`timing_sma` (which also beats the candidate on the committed Sharpe metric). Low drawdown does not
rescue a signal that underperforms the simpler timer on the primary risk-adjusted metric (Decision #8).

**Decision: PARK `spy_short_reversal`.** A parked candidate is a valid workflow outcome. Per the
locked one-shot rule, the OOS is now burned — no parameter iteration on 2015+. Do **not** proceed to
§8 paper / §9 live. **Decision #10 (QQQ second instance) is moot** — do not extend a parked signal to
a 0.9-correlated instrument. Salvage value: the **200-day timing overlay itself** is the part that
survived OOS (timing_sma 0.63 Sharpe, −14.7% DD, beats B&H risk-adjusted on this window net of high
cash rates) — if anything here is worth a future look, it is the plain timing overlay, not the
reversion entry. Logged to `ROADMAP.md` → Decided Against.

---

## 8. Paper Trading

Wire into `run_live.py` via the `STRATEGY_NAME` / `STRATEGY_PARAMS` config block; paper account
(`DUxxxxxxx`, TWS port `7497`).

**What paper validates here is plumbing, not edge.** A daily strategy fires few signals, so a
weeks-to-months paper window has far too few trades to confirm the statistical edge. Paper
trading is validating:

- The execution path end-to-end: signal fires → `OrderRequest` → `order_from_request` →
  `placeOrder` → fills persist → `position_snapshots` update.
- **Live/backtest signal parity** — on the same bars, the live strategy must emit the same
  signals the backtester did. This is the core design guarantee (`STRATEGY.md`); a parity break
  is a bug, not a market event. **Concretely for this candidate: the live fetch must use
  `whatToShow="ADJUSTED_LAST"`** (the configurable flag from §2). If live runs on `TRADES` while
  research/backtest ran on `ADJUSTED_LAST`, signals will diverge around ex-div dates — verify the
  live config sets it before trusting parity.
- The trend filter gating correctly on live data.
- Behavior across a TWS disconnect (currently a known gap — `IBKR_NOTES.md` 1100/1102; no
  auto-reconnect yet).

---

## 9. Pre-Live Checklist → Live Small

**Do not connect a live account until the account-level risk layer exists.** Several controls are
documented as required-before-live and are **not yet built** (`RISK.md` → Known Gaps,
`ROADMAP.md` → Backlog):

- [ ] **System-wide per-order size limit** in `orders/order_handler.py::place_order()` — rejects
      a buggy oversized order before `placeOrder`. (Not built.)
- [ ] **Kill switch** — flag checked in `place_order()`, halts all strategy instances. (Not built.)
- [ ] **Connection-alive guard** — no orders placed against a disconnected app. (Not built.)
- [ ] **Daily loss limit** — session circuit breaker reading realized + unrealized PnL from
      `self.account`. (Not built.)
- [ ] Account number and port switched to live (`7496`); `ACCOUNT_NUMBER` changed from `DUxxxxxxx`.
- [ ] Secrets manager in place (no hardcoded account/credentials) — `ROADMAP.md` backlog.

### Sizing against the floor (design constraint, not advice)

Starting capital $50k, hard floor $25k. Because a dip-buyer **adds exposure as price falls**,
size against the worst case "positioned and the dip keeps going," not the average trade. The test:
a 2008/2020-magnitude adverse move while fully positioned must still leave equity **comfortably**
above $25k — design risk limits against the floor, not against starting equity (`RISK.md`).
(For an unlevered single-SPY position the floor is unlikely to bind — see §1, the likely failure
is mediocrity, not ruin — but the discipline stays: it is the backstop, and it stops being
slack the moment leverage, scaling-in, or a second correlated instrument enters. Position sizing
is a design parameter to stress-test; this is not investment advice and I'm not a licensed advisor.)

### Live small

First live deployment runs at a fraction of research size, monitored closely, before any scaling.
Never skip paper → small → scale.

---

## Open Decisions

Fresh list for this candidate. Resolve and mark settled as research progresses.

| # | Decision | Status |
|---|---|---|
| 1 | **Signal family** — RSI(2) (Option A) vs deviation (Option B) | **Resolved → Option A** (higher marginal edge: +0.56% vs +0.25%/5d) |
| 2 | **Dividend/split adjustment** — adjusted vs raw, applied consistently | **Resolved → `ADJUSTED_LAST`** (total-return) everywhere via configurable `whatToShow`; default stays `TRADES`. Splice clause dropped (single-source IBKR, moot). |
| 3 | **IS/OOS split dates** + reserved stress regime | **Resolved/LOCKED → IS ≤ 2014-12-31, OOS 2015+** (contains 2018/2020/2022) |
| 4 | **Exit rule** — mean-cross vs short-MA-cross vs fixed time-stop | **Resolved → single short-MA-cross: `close > SMA(5)`** (the canonical RSI(2) exit; one rule, not compound). Frozen in `spy_short_reversal` §4. |
| 5 | **Trend-filter length** — 200-day default; test 100/150 for robustness only | **Resolved → 200** (§5). Sweep {100,150,200,250} → Sharpe {0.59, 0.60, **0.78**, 0.67}; 200 is the robust sweet spot, shorter filters degrade gracefully to ~0.60 (still > B&H 0.45). Not knife-edge. |
| 6 | **Sizing approach** — fixed shares vs fixed notional vs vol-scaled; account for the $1 commission-minimum bps drag | **Resolved (for §4) → fixed-notional single entry ($50k, `target_notional`)**: ~0.56 bps/fill drag, $1 minimum never binds (0/262 fills). Non-compounding by design; revisit vol-scaling in §5 if warranted. |
| 7 | **OOS pass/fail criteria** — commit numeric gates *before* the one-shot | **Resolved/LOCKED → see §7 pre-registration (G1–G5).** Convention (b). G1 beats both benchmarks on Sharpe (binding = timing-only); G2 OOS Sharpe ≥0.40; G3 maxDD shallower than −20%; G4 ≥40 round-trips; G5 filter mutes downturn. PASS = G1–G4 ∧ G5; else park, no re-tune. |
| 8 | **Benchmark metric** — bar is **both** buy-and-hold SPY and timing-only ("flat below 200-day"); on which metric. Timing-only (Sharpe 0.50) is the binding bar. | **Resolved → Sharpe (primary) + max drawdown.** §4: candidate 0.78 beats timing-only 0.50 and B&H 0.45; absolute return is *not* the metric (candidate sits in cash). |
| 9 | **Entry style** — single entry vs scaling in (scaling worsens floor risk) | **Resolved → single entry** (enter only when flat; no averaging down). Implemented + unit-tested in §4. |
| 10 | **QQQ second instance** — defer until SPY validates standalone | **Moot → dropped.** SPY did not validate (§7 FAIL → parked); do not extend a parked, OOS-failed signal to a 0.9-correlated instrument. |
| 11 | **Old `research/` cleanup** — leave legacy vs remove | **Partially actioned** — scratch builders removed during §3; legacy notebook/RF pipeline left in place (default). `session.py` retained (load-bearing). |
| 12 | **Parity: live fetch flag** — live `run_live.py` must set `whatToShow="ADJUSTED_LAST"` to match research/backtest | **Configurable flag landed (§4)** — `run_live.py::WHAT_TO_SHOW` (default `TRADES`). **Still TODO before paper (§8):** set it to `ADJUSTED_LAST` when `spy_short_reversal` is wired into `run_live`. |
| 13 | **Idle-cash / Sharpe convention** — credit idle cash at the short rate (excess-over-cash) vs hold at 0% | **Resolved → convention (b)** (excess-over-3M-T-bill, idle cash credited at the same series), applied consistently to all three legs and **locked before viewing OOS**. The §5 capability (`compute_metrics(risk_free_rate=...)`) plus the script's idle-cash crediting implement it; the engine default stays rf=0 (research applies (b) in the reporting layer). |

---

## Candidate Decisions to Document Elsewhere

Surfaced during framing; not specific to this candidate, worth recording in the system docs:

- **Equity autocorrelation-horizon map** (reversion at days, dead zone at weeks, momentum at
  months) as the standing reason a candidate's horizon must match its effect → `STRATEGY.md`.
- **Parallel single-symbol *equity* strategies do not reproduce TSMOM diversification** because
  equities co-move (shared market beta) — so it shouldn't be re-proposed as a diversification
  play → `STRATEGY.md` / `ROADMAP.md`.
- **Long-only timing-overlay strategies must be benchmarked against the timing filter alone**,
  not just buy-and-hold, to attribute edge to the signal vs. the filter → `BACKTESTING.md`.
- **Adjustment basis is a signal-vs-equity-curve distinction:** return-based signals (RSI, SMA)
  are nearly invariant to dividend adjustment, but the equity curve and any total-return benchmark
  are not — standardize on total-return (`ADJUSTED_LAST`) for equity strategies, and keep
  research = backtest = live on one basis → `BACKTESTING.md` / `DATA_MODEL.md`.
- **`whatToShow` should be a first-class, configurable parameter** through the fetch/loader/config
  path (it was hardcoded to `TRADES`); record the configurable-flag pattern → `IBKR_NOTES.md`.