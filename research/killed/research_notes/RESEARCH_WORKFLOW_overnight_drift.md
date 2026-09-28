# Research Workflow — Overnight Drift (Close-to-Open Premium)

> **STATUS: STOPPED at Step 3 — overnight premium does not beat buy-and-hold net of
> turnover costs.** The gross overnight Sharpe (0.50) beats B&H (0.41), but the ~252
> round-trips/year cost (even at an optimistic 1 bp RT) destroys the margin: net overnight
> Sharpe = 0.27 vs B&H 0.41. Annualized return: 3.4% net overnight vs 7.4% B&H. The failure
> is structural (cost asymmetry), not decay — the edge is roughly stable across IS years.
> Tail gate passes (survivable at all deployment fractions). OOS was never opened.
> EDA: `research/killed/overnight_drift/eda.py` + plots. Decision point below.
>
> Successor candidate to the intraday-conditional strategy
> (`intraday_conditional_strategy_notes.md`), which was **killed at Step 3 (H1 rejected
> in-sample)**. Follows the same step structure as that doc and the spy_short_reversal notes.

---

## Strategy Summary

One sentence: **hold one liquid US equity index ETF long from the closing auction to the
next opening auction (the overnight session), and sit in cash through the regular trading
day — harvesting the documented tendency of index drift to accrue overnight rather than
intraday.**

The position is opened with a market-on-close (MOC) order and closed with a market-on-open
(MOO) order the next morning. Flat (in cash) during every regular session. No shorting, no
intraday leg.

> **Note — this is not a day trade.** Buying at close N and selling at open N+1 is an
> *overnight hold*; the position spans two sessions. It does **not** generate a PDT pattern
> day trade (unlike the intraday candidate, where one round trip per day was a guaranteed
> PDT pattern). The $25k PDT *floor* still binds — but via drawdown, not via day-trade
> counting. That distinction matters for this candidate's risk profile (below).

---

## Hypothesis

**H1 (primary).** For a liquid index ETF, the **overnight return**
`r_on(t) = (open(t+1) − close(t) + div(t+1)) / close(t)` has a positive risk-adjusted
expectation that materially exceeds the **intraday return**
`r_id(t) = (close(t) − open(t)) / open(t)`, such that capturing overnight-only exposure
(flat intraday) produces a higher Sharpe than full-day buy-and-hold, **net of turnover
costs and net of the cash credited on idle daytime capital.**

`div(t+1)` is the dividend (if any) going ex on the morning of `t+1`, added back because the
ex-dividend price drop at the open is a mechanical artifact, not a tradeable loss (see
"Ways the backtest will lie to me" → ex-dividend trap).

**Economic rationale (candidate mechanisms — these are competing, and which one is true
changes whether this is worth trading):**

1. **Overnight gap-risk premium.** Markets are closed ~17.5h/day; positions can't be hedged
   or exited and news is impounded at the open. Holding overnight bears uninsurable gap
   risk, so it earns compensation. *If this is the mechanism, the edge is beta to overnight
   tail risk, not alpha — you are being paid to hold the left tail, and you pay it back in
   crash gaps.* This is the interpretation that should keep us honest.
2. **Clientele / order-flow timing (Lou, Polk & Skouras, 2019, JFE — the canonical
   reference).** Retail and news-driven demand concentrates around the open (captured by the
   overnight return); institutional rebalancing and VWAP execution press prices intraday.
   Persistent, distinct clienteles trade at different times. *If this is the mechanism, it's
   a genuine inefficiency, but one subject to crowding/decay once published.*
3. **Capacity-limited inefficiency.** Auctions can only absorb so much; large players can't
   fully harvest the open/close prints, leaving crumbs for small accounts. *If this is the
   mechanism, it's real and capturable specifically at our size — but the persistence is
   precisely because it doesn't scale, which is fine for $50k.*

**What I expect to find if this works.** A naive IS backtest that looks **too good** —
high hit rate, smooth equity curve, Sharpe in the ~0.8–1.3 range, low day-to-day vol. That
is the base rate for this anomaly in-sample and is *not* evidence of a tradeable edge. The
real signal is in three places the naive backtest doesn't show: (a) whether the edge has
**decayed** in the post-publication OOS window, (b) whether the drawdown is **lumpy and
gap-driven** (left tail), and (c) whether it survives an **honest cash credit** on the idle
daytime capital (at 5% cash rates, sitting in cash all day is a large, separable return that
must not be mistaken for overnight edge).

**What I expect the realistic net profile to be**, after a decay haircut and a tail-risk
adjustment: OOS net Sharpe maybe **0.4–0.8**, with the explicit caveat that **Sharpe
overstates the quality of a left-tail-risk-premium strategy** (it penalizes vol
symmetrically while the risk here is one-sided). Max drawdown modest in normal regimes
(single digits to low-teens %) but **lumpy in crash-gap clusters** (COVID Feb–Mar 2020 is
the stress case) — which is exactly why position sizing and the floor interact (below).

**What would falsify the hypothesis.**
- The overnight premium is **gone in the recent OOS window** (decayed to ≈ 0, or no longer
  beats buy-and-hold risk-adjusted) → stop. This is the most likely way it dies.
- Once idle daytime cash is credited at the same rate it's subtracted as the risk-free rate,
  overnight-only **does not beat buy-and-hold on Sharpe** → the apparent edge was the cash
  tailwind, not the overnight premium → stop.
- The worst clustered overnight drawdown at any defensible deployment fraction **breaches the
  $35k backstop** → the tail is not survivable at this capital → stop (or only viable at a
  deployment fraction so small the strategy isn't worth running).

**Anti-pattern to avoid (carried over from the intraday doc).** If H1 fails, this candidate
stops. It does **not** pivot to "well, maybe the short-intraday leg works" or "maybe a
volatility filter rescues it." Those are different strategies and deserve their own workflow
docs and their own pre-committed OOS.

---

## Why this is a different bet than the strategies already killed

- **vs. intraday-conditional (killed Step 3):** that bet was "first-hour move magnitude
  predicts rest-of-day direction" — a *conditional* signal that turned out to be noise +
  gap momentum. This is an *unconditional* structural premium that is not in doubt
  in-sample; the question is decay/tail/cost, not existence. Different failure surface.
- **vs. spy_short_reversal (parked at OOS):** that was a crowded textbook mean-reversion
  signal that turned out to be a 200-day timing overlay. Overnight drift is *also* crowded
  and published (same publication-bias caution applies), but its mechanism (a recurring
  premium, not a fade signal) is structurally different, and its binding benchmark is
  buy-and-hold rather than a timing overlay.

---

## Kill Criteria (committed before any backtest)

Committed now, before looking at IS data. Any one triggers a stop. Win-rate and profit-factor
gates are **deliberately omitted** — a drift-harvesting strategy has an intrinsically high hit
rate that carries no information, and over-weighting it would be self-deception.

| Criterion | Threshold | Where evaluated |
|---|---|---|
| IS net Sharpe (excess over cash) vs buy-and-hold | candidate must beat B&H Sharpe by ≥ **+0.2** | Step 4 |
| IS max drawdown | > **15%** of allocated capital | Step 4 |
| IS worst clustered 5-session overnight loss | projects equity **< $35k** at chosen deployment | Step 4 (tail gate) |
| Parameter sensitivity (sizing fraction, ex-div handling, session-edge defs) | best Sharpe > **2×** median of neighborhood | Step 5 (overfit signal) |
| Cost + execution stress (2× costs, adverse-auction scenario) | net Sharpe < **0.5** | Step 6 |
| OOS net Sharpe vs IS | OOS < **50%** of IS net Sharpe | Step 7 (decay gate) |
| OOS net Sharpe vs buy-and-hold | OOS does **not** beat B&H Sharpe | Step 7 |
| OOS max drawdown | > **15%** | Step 7 |
| Paper-trade tracking error vs backtest | > **50 bps/trade** unexplained (esp. MOC/MOO fills) | Step 8 |

**The tail gate is the one to take seriously.** A long-overnight strategy eats overnight
gap-downs in full (held from prior close through the gap open). At full $50k deployment a
single −7% overnight gap is −$3,500 (recoverable); a *clustered* crash where the position is
re-entered into a falling tape each close (COVID) is the scenario that can compound toward the
floor. **Drawdown control here is a hard constraint, not a soft preference** — same posture as
the intraday doc, same $35k absolute backstop ($10k above the $25k PDT floor), consistent with
`RISK.md`. This gate is the most likely driver of the position-sizing decision (fractional, not
full deployment).

---

## Ways the backtest will lie to me

This candidate's failure modes are mostly *measurement and execution artifacts*, not absence
of signal. Enumerated explicitly so they're designed against, not discovered late.

1. **Marking fills at official close/open prints as free.** The backtest will use `close(t)`
   and `open(t+1)`. The honest question is whether those prints are *achievable*.
   - *At our size on a top-liquidity ETF, mostly yes:* MOC and MOO orders participate in the
     closing/opening auctions and receive the official auction clearing price. A $50k order is
     a rounding error in auctions that clear billions — market impact ≈ 0, and auctions don't
     have a "spread you cross." Commission on a ~$500–600 ETF at ~$0.0035/share is ~0.06 bps/side.
   - *So the folklore that overnight is "untradeable" is largely an institutional-size and
     marketable-order artifact and is much weaker for us.* **But** Step 6 must still stress an
     adverse-auction scenario (volatile mornings, occasional inability to MOO, auction price vs
     last-look), because the per-trade edge is small enough that even modest execution slippage
     consumes a meaningful fraction of it.
2. **Ex-dividend overnight returns as artifacts (the dividend trap).** On an ex-dividend
   morning the ETF opens lower by ≈ the dividend. On TRADES (unadjusted) data those ~4
   mornings/year show a spurious ≈ −30–40 bps "overnight loss" that is **not** a real loss —
   you receive the dividend. You must **add the dividend back** to the ex-div overnight return.
   This is why the standard project `ADJUSTED_LAST` convention is **wrong for this decomposition**
   (multiplicative back-adjustment distorts the additive close→open gap exactly on the dates that
   matter) — see Open Decisions #2.
3. **Sharpe flatters a left-tail-risk premium.** If mechanism #1 (gap-risk premium) is true,
   the return stream is one-sided-tail-bearing. Sharpe rewards the smooth body and under-counts
   the rare deep gap. Report **drawdown distribution, worst-N-day clustered loss, and skew/kurtosis
   of `r_on`** alongside Sharpe, and weight them in the verdict. Do not let a clean Sharpe override
   an ugly tail.
4. **Cash-rate tailwind masquerading as edge.** The strategy is in cash ~6.5h every session
   plus weekends. At ~5% cash rates that idle credit is a large, *separable* return. If the
   benchmark isn't credited cash identically, overnight-only looks better than it is. **Lock the
   excess-over-cash convention** (idle cash credited at the same rate subtracted as `risk_free_rate`)
   — far more load-bearing here than it was for spy_short_reversal, because this strategy sits in
   cash a much larger fraction of the time. See Open Decisions #8 and `BACKTESTING.md` → cash
   convention.
5. **Secular decay / publication crowding.** Lou-Polk-Skouras is 2019; the SPY-overnight fact has
   been in the practitioner press for a decade. If it were cleanly capturable net of real costs at
   scale it would be more arbed. The recent OOS window is the test — and OOS is therefore weighted
   toward the *most recent* years, where decay would bite (see Open Decisions #4).
6. **Fill-model look-ahead in any sizing overlay.** The *entry is unconditional* (long every
   night), so filling the entry at the signalling bar's own close is **lookahead-safe for this
   strategy specifically** (you commit the MOC before the close prints; the decision doesn't use
   the close). But if a later version conditions sizing on same-day close-derived features (vol
   targeting, etc.), that reintroduces look-ahead. Keep v1 unconditional.
7. **Survivorship — N/A here, and that's a feature.** A single ETF that exists throughout the
   window has no survivorship bias, unlike a single-name cross-sectional version of this anomaly.
   Using an ETF deliberately sidesteps it.

---

## Open Decisions

Decisions 1–4, 7, 8 **RESOLVED at Step 2** (2026-06-19). 5 (deployment fraction), 6 (execution
model), and the fill-semantics flag remain **OPEN** (deferred to Step 4 / their gating step).

1. **Universe — RESOLVED: SPY only.** Single ETF, per the single-symbol mandate. SPY over QQQ for
   three reasons: (a) **clean sample** — 2015–2021 QQQ was partially "seen" for gap behavior during
   the intraday EDA, and the overnight return *is* essentially the open gap, so QQQ in that window
   is contaminated for this hypothesis; (b) **best execution** — SPY has the deepest, tightest
   closing/opening auctions, the crux for this strategy; (c) **longest history**.
2. **Data basis — RESOLVED: TRADES (split-adjusted) + IBKR-derived dividend overlay.** Deviation
   from the house `ADJUSTED_LAST` convention, and justified: the ex-dividend price drop lands at
   the **open** — exactly what the close→open overnight return measures — and multiplicative
   back-adjustment (ADJUSTED_LAST) *smears* that additive gap across the dates that matter (trap
   #2). So we need *unadjusted* OHLC plus an additive dividend add-back. **The deviation is
   documented at the basis's configuration site** — the package builder docstring
   (`research/killed/overnight_drift/build_dividend_calendar.py`); it will be echoed to `IBKR_NOTES.md` /
   `BACKTESTING.md` when a `BacktestConfig` consuming this basis lands (Step 4), per the "document
   when it lands" note. **Dividend source — RESOLVED: derive from IBKR**, by differencing the two
   cached IBKR daily series (`ADJUSTED_LAST` total return − `TRADES` price return); the gap on a day
   isolates that day's cash dividend. Same vendor as the price data ⇒ ex-dates align to the exact
   trading calendar by construction (no cross-source burden). **Coverage caveat:** IBKR's
   ADJUSTED_LAST only encodes SPY dividend adjustments from ~2006-03 onward (the TRADES/ADJUSTED_LAST
   ratio is frozen across 1997–2005), so the calendar is complete 2006+ but **empty 1997–2005** and
   sparse 1993–1996. No external pre-2006 source for v1 (explicit decision) — it is a later optional
   extension only. Consequence for Step 3: **none** — IS now starts 2006-01-01 (Open Decision #4),
   where the overlay is complete, so every in-sample ex-div morning is corrected. The empty 1997–2005
   window is pre-IS; the prior "uncorrected ~−20–30 bps artifact on ~4 mornings/yr" caveat is retired.
3. **History depth / source — RESOLVED: IBKR daily, full depth = SPY inception.** Probe result:
   IBKR serves SPY daily TRADES back to **1993-01-29** (SPY's listing date — a hard floor, nothing
   exists before it), deeper than the proposed 2004 start. So the dot-com top, 2008, and COVID are
   all in-sample. Daily bars only; no intraday data needed for v1 (open/close come from daily OHLC).
4. **IS / OOS split — RESOLVED & COMMITTED: IS 2006-01-01 → 2018-12-31, OOS 2019-01-01 → present.**
   (The 2018|2019 boundary is the fixed decay-catching cut and is unchanged — no iteration on it.)
   **The IS start moved from the 1993-01-29 data floor to 2006-01-01 — a deliberate exclusion of
   pre-GFC, not a data limitation.** The full 1993→present series stays cached in `market_data_bars`
   (no re-pull); 2006-01-01 is the IS-start *usage* cut only. Two reasons: (a) the IBKR-derived
   dividend overlay is complete 2006+ (Open Decision #2), so the **entire IS is dividend-corrected** —
   this retires the prior "ex-div returns in 1997–2005 stay uncorrected" caveat (now moot); and
   (b) pre-2006 NYSE auction microstructure (pre-Hybrid Market / pre-Reg NMS) is less representative
   of the current opening-print mechanics this signal depends on. Consequence for the documented data
   gaps: **2004-07-12 is now pre-IS and irrelevant**; **2007-07-02 remains in-IS** and still needs the
   drop-both-overnights-spanning-it handling in Step 3. OOS is weighted to recent years on purpose:
   decay is the primary kill risk, so the held-out window covers the post-publication / post-crowding
   era (COVID gap stress + rate-regime shift). **Committed to no iteration on OOS.**
5. **Position sizing / deployment fraction — OPEN (Step 4).** Full notional vs fractional. Driven
   by the tail gate: if full deployment breaches the $35k backstop on the worst clustered overnight
   loss, fractional is mandatory. Default proposal: size so the IS worst clustered 5-session
   overnight loss leaves projected equity ≥ $35k.
6. **Execution model — OPEN (lock before Step 6).** Assume MOC entry / MOO exit at official auction
   prints (defensible at our size). The open part is **how conservatively to stress it** — adverse
   fill of +X bps on volatile mornings, and a scenario where the MOO occasionally can't be placed.
7. **Structure variant — RESOLVED: v1 is long-overnight-only.** The full Lou-Polk-Skouras "tug of
   war" adds a short-intraday leg, **out of scope for v1**: it needs shorting (borrow cost; long-only
   today per `RISK.md`) and short-at-open + cover-at-close **is a same-session round trip = a PDT
   pattern day trade**, reintroducing exactly the problem this candidate avoids. Flat-in-cash
   intraday. The short-intraday variant, if ever pursued, is a separate workflow doc.
8. **Cash convention — RESOLVED & LOCKED: excess-over-3M-T-bill, idle cash credited at the same
   rate subtracted as `risk_free_rate`** — identical treatment for candidate and benchmarks. Reuses
   the **single authoritative** T-bill series already in the repo — `TB3MS_ANNUAL_PCT` +
   `_daily_rate(year)` in `research/killed/spy_short_reversal/sensitivity.py` (FRED TB3MS annual averages).
   No second series is introduced. More material here than for any prior candidate (this strategy
   sits in cash ~6.5h/session + weekends). Applied at Step 4; both-ways comparison pattern in that
   same `sensitivity.py`.

---

## Architecture flag — fill semantics (confirm against code in Claude Code)

This strategy stresses the backtest engine's fill model differently from prior candidates and the
question should be settled **before** Step 4, in Claude Code, against the actual `SimBroker`:

- The faithful execution is **entry at this bar's close (MOC)** and **exit at next bar's open (MOO)**.
- The engine's default `next_open` fills a signal at the *next* bar's open — wrong time for the
  *entry* leg of an overnight hold.
- `BACKTESTING.md` notes a `fill="close"` model exists but is "optimistic and not lookahead-safe
  *in general*." For *this* strategy the entry is **unconditional**, so filling the entry at the
  signalling close introduces **no look-ahead** (the decision doesn't use the close). So a faithful
  backtest likely = entry `fill="close"` (safe here) + exit at next open.
- **Open question for Claude Code:** can `SimBroker` express per-leg fill timing (close for entry,
  next-open for exit), or does faithful MOC/MOO support need to be added? This is a real-but-modest
  infra question; do **not** assume the answer from the docs. The research EDA (Step 3) sidesteps it
  entirely (it computes the overnight-return stream analytically in pandas, no engine), but Step 4
  onward must run through the real engine for live/backtest parity.

> Note this does **not** reopen the "Decided Against: vectorized backtesters" call. The Step 3 EDA
> being a return-stream computation is the same tier as every prior EDA notebook (pure pandas, no
> engine). Validation (Step 4+) still runs through the custom event-loop engine. The
> inventory-awareness rationale for that engine simply doesn't *bind* for this strategy (it's flat
> every morning by construction, no position gating) — but parity does, so the engine remains the gate.

---

## Workflow

### Step 1 — Framing
**Objective.** Hypothesis, economic rationale, expected profile, kill criteria, open decisions.
(This document.)
**Done when.** The above are written and unchanged for 24h of reflection.

### Step 2 — Universe and Data  ✅ COMPLETE (2026-06-19)
**Done.** SPY daily TRADES loaded into `market_data_bars` (8402 bars, 1993-01-29 → 2026-06-18,
date-only ISO, cached under `what_to_show='TRADES'`); IBKR-derived ex-dividend calendar written to
`research/killed/overnight_drift/spy_dividends.csv` (85 ex-dates, 1993–2026, aligned to ex-div mornings).
All QC passed (see `research/killed/overnight_drift/README.md` for the full readout). One finding carries
into Step 3: of the 2 unrecoverable IBKR daily gaps, **2004-07-12 is now pre-IS (irrelevant)** while
**2007-07-02 remains in-IS** and still needs the drop-both-overnights-spanning-it handling in Step 3.
The other prior carry-forward — the empty 1997–2005 dividend window (IBKR limitation, Open Decision
#2) — is now **moot**: IS starts 2006-01-01 (Open Decision #4), where the overlay is complete and
every in-sample ex-div morning is corrected.

**Universe.** One ETF (recommend SPY). No other symbols, no single names, no futures.
**Data.** Daily OHLC for the full IS+OOS window, **TRADES (unadjusted)** basis, plus a **dividend
calendar** for ex-div correction. Into `market_data_bars` per `DATA_MODEL.md` (ISO datetime,
`(symbol, bar_size, datetime)` unique key, `what_to_show` as cache key so TRADES never collides
with any cached ADJUSTED_LAST series).
**Quality checks.** No missing trading days; dividend dates correctly aligned to ex-div *mornings*;
spot-check 5–10 ex-div opens against a reference (the open should drop ≈ the dividend); if an
external pre-2004 source is used, **cross-source parity** vs IBKR over the overlap window (OHLC
within a few bps).
**Done when.** Data loaded, checks pass, IS/OOS split locked in writing here.

### Step 3 — Signal Exploration (Notebook)  ⛔ STOPPED (2026-06-19)
**Where.** `research/killed/overnight_drift/eda.py` + 4 plots in `research/killed/overnight_drift/`.
**Corrections applied vs the original Step 3 spec:**
- **OOS-leak fix:** the doc's tail study said "through Feb–Mar 2020 specifically" — that is OOS
  (2019+). Replaced with IS clusters (2008 GFC, 2011, Aug-2015, Feb/Dec-2018).
- **Net-of-cost overlay:** the doc's Step 3 was gross-only for the decision comparison. Added an
  illustrative net-of-cost overnight Sharpe (bracket: 0.5 / 1.0 / 2.0 bp round-trip) because
  overnight trades ~252 RT/yr vs B&H ~0 — a gross comparison flatters overnight by ~250 bp/yr.

**Results (IS only, 2006-01-01 → 2018-12-31, 3268 trading days):**

1. **Cumulative decomposition** (`01_cumulative_decomposition.png`): overnight (2.1×) outperforms
   intraday (1.2×) but full-day B&H (2.6×) finishes higher — the compounding gap from being
   invested all day dominates.
2. **r_on distribution**: mean = 2.6 bp/day (6.5% ann.), std = 10.8% ann., skew = −0.53,
   kurtosis = 16.9 (extreme excess). Confirms left-tail-risk premium character. Worst: −838.7 bp
   (2008-10-24). 2008 and 2015 dominate the worst-20 list.
3. **Sharpe comparison — THE BINDING RESULT:**
   - Gross: overnight 0.50 > B&H 0.41 > intraday 0.09. Overnight beats B&H gross.
   - **Net of cost: overnight 0.27 (at 1 bp RT) < B&H 0.41.** Delta = −0.14.
   - Even at 0.5 bp RT (optimistic floor): net overnight 0.38, still < B&H 0.41.
   - At 2 bp RT (conservative): net overnight 0.03 — essentially zero.
   - Only 4 of 13 IS years does net overnight beat B&H on Sharpe.
   - Annualized return: 3.4% net overnight vs 7.4% B&H.
   - Recent-IS (2015–2018): net overnight 0.43 vs B&H 0.52 — same pattern, no rescue.
4. **Ex-div sanity** (aggregate): raw ex-div overnight mean = −15.8 bp vs non-ex-div = +2.1 bp
   (gap = −17.9 bp). After add-back: ex-div = +34.8 bp, non-ex-div = +2.1 bp (gap = +32.7 bp).
   Mean add-back = +50.6 bp, consistent with mean div ($0.84) / mean price (~$160) ≈ 52 bp.
   The corrected gap being positive rather than zero is likely small-sample noise (N=50 ex-div
   days, clustered in opex weeks).
5. **Tail study** (`05_tail_gfc_equity.png`): all IS clusters survivable at all fractions.
   Worst cluster (2008 GFC Sep–Nov, 63 days): −14.4% cumulative overnight return → $42,788 at
   100% deployment, well above $35k backstop. Worst rolling 5-session loss: −14.0% (ending
   2008-10-27). At 50% deployment → $46,494 (survives). **Tail gate passes.**

**Decision point (STOP — does not proceed to Step 4).**

The overnight premium exists in-sample (Sharpe 0.50 gross, clearly real, not concentrated in early
years) and the tail is survivable. But it **fails the binding gate: net of turnover costs, overnight
does not beat buy-and-hold risk-adjusted.** The failure mechanism is the **cost asymmetry**:
- Overnight trades ~252 round-trips/year; B&H trades ~0.
- At 1 bp RT (IBKR tiered commission + placeholder auction slippage, the realistic middle bracket
  per BACKTESTING.md), the annual cost is ~252 bp = ~2.5%.
- The gross overnight edge over B&H (0.50 − 0.41 = +0.09 Sharpe) is too small to absorb this.
- The Sharpe advantage **reverses** at any positive cost assumption — overnight net loses to B&H.

This is not a decay failure (the edge is roughly stable 2006–2018) and not a tail failure (passes at
all fractions). It is a **structural cost failure**: the per-trade edge (~2.6 bp/day) is too thin to
pay for ~252 round-trips/year, while B&H earns the same market return compounded and pays ~nothing.
Exactly the mechanism predicted in "Ways the backtest will lie to me" → #1 (fills ≠ free) and #4
(cash-rate tailwind). The overnight premium is a genuine feature of index returns, but it is not a
**tradeable** edge at any realistic cost structure — not at $50k, not at $50M.

**The candidate is STOPPED.** OOS was never opened. No strategy code, no engine work, no
fill-semantics resolution needed.

### Step 4 — In-Sample Backtest
**Objective.** Single IS run through the real engine at the chosen deployment fraction, evaluated
against kill criteria. Resolve the fill-semantics flag (above) first.
**Cost assumptions.** Commission per `BACKTESTING.md` (IBKR tiered); auction execution at official
prints with a small assumed slippage. Conservative version is Step 6.
**Outputs.** Equity curve, trade log, metrics (Sharpe excess-over-cash, max DD, **tail/clustered
loss**, total commission), three-way vs `buy_and_hold` and an intraday-only baseline.
**Done when.** A kill triggers (stop) or all pass → Step 5.
**Anti-pattern.** Re-tuning the deployment fraction after seeing IS Sharpe. Fraction is set by the
tail gate, not by return maximization.

### Step 5 — Parameter Sensitivity
**Objective.** Confirm robustness, not optimize. This strategy has few parameters — that's good.
**Sweep.** Deployment fraction (neighborhood around the Step 4 choice); ex-div handling on/off (to
confirm it's a correction, not a tuning knob); session-edge definitions (official auction vs first/
last 5-min VWAP, if intraday bars are pulled for execution realism).
**Pass.** Chosen set within ~0.3 Sharpe of neighborhood median on each axis; no single spike > 2×.
**Done when.** Plots saved, stability confirmed, or kill triggers.

### Step 6 — Cost & Execution Stress
**Objective.** Survive realistic-pessimistic auction execution — the gate that matters most given
how small the per-trade edge is.
**Method.** 2× commission; adverse-auction slippage scenario (Open Decision #6); a scenario where
the MOO occasionally fails and the exit slips to a marketable order crossing the open spread.
**Pass.** Net Sharpe > 0.5.
**Done when.** Documented or kill triggers.

### Step 7 — Out-of-Sample (One-Shot)
**Objective.** Single, final, no-iteration run on the held-out recent window — **the decay gate.**
**Rules.** Run once, parameters and code locked from Step 6. OOS Sharpe must retain ≥ 50% of IS and
still beat buy-and-hold. "A different deployment fraction would pass OOS" is the forbidden sentence.
**Done when.** OOS metrics computed, compared to IS, decision: paper or kill.

### Step 8 — Paper Trading
**Objective.** Validate that live MOC/MOO fills reproduce the backtested auction prints under real
broker semantics. The crux is **fill quality at the auctions** — log every MOC/MOO submit time,
the auction print received, and the backtest-assumed price, per trade.
**Duration.** ≥ 4 weeks, ideally spanning at least one ex-dividend date and one elevated-vol morning.
**Pass.** Per-trade tracking error < 50 bps, no unexplained outliers; ex-div handling reconciles live.
**Done when.** Paper reconciles to backtest within tolerance for the full window.

### Step 9 — Pre-Live Checklist
Cross-ref `RISK.md`. Same operational gates as the intraday doc, plus the items specific here:
- [ ] MOC/MOO order construction implemented and tested in `orders/` (this is new order-type work).
- [ ] System-wide per-order share-size limit (known `RISK.md` gap).
- [ ] Kill switch + daily-loss-limit flags implemented and tested.
- [ ] Deployment fraction enforced; overnight gap-down stress checked against the $35k backstop.
- [ ] Dividend calendar maintained / auto-refreshed so live ex-div handling matches backtest.
- [ ] Account/port switched paper → live; reconnect + stale-data guards verified (IBKR daily restart
      must not leave a position un-exited at the open).
- [ ] Position reconciliation at session start (held-overnight position matches IBKR).

### Step 10 — Live Small
Smallest informative size, ≥ 4 weeks, every trade tracked vs backtest. Scale only on operational
cleanliness + tracking error in tolerance + realized Sharpe within 1σ of OOS — never on early P&L.

---

## Cross-References
- `STRATEGY.md` — strategy interface, `OrderRequest`, registry, single-symbol (N=1) shape.
- `BACKTESTING.md` — fill assumptions, **cash convention**, benchmarking (buy-and-hold is binding here).
- `DATA_MODEL.md` — `market_data_bars` schema, datetime normalization.
- `IBKR_NOTES.md` — `whatToShow` basis, daily-bar depth, MOC/MOO order placement, error codes.
- `RISK.md` — long-only posture, $25k floor / $35k backstop, missing order-layer controls.
- `ROADMAP.md` — order-layer risk controls and reconnect logic are backlog blockers for Step 9.

---

## Handoff summary for Claude Code

Resolve the Open Decisions first (universe = SPY recommended; **TRADES + dividend overlay**, not
ADJUSTED_LAST — note the justified deviation; history depth; IS/OOS split; deployment fraction
deferred to Step 4; cash convention locked). Most are research/operational, not coding decisions.

First code touchpoints, in order:
1. **Settle the fill-semantics question** against the actual `SimBroker` (can it do entry-at-close /
   exit-at-next-open per leg, or does MOC/MOO support need adding?) — this gates Step 4. Do not infer
   from the docs; read the code.
2. **Step 2 data load**: daily SPY TRADES into `market_data_bars` + a dividend calendar + the ex-div
   add-back, with the quality checks above. Hand off with: chosen source, chosen history depth,
   committed IS/OOS dates, and a link to this doc.
3. **Step 3 EDA notebook** in `research/killed/overnight_drift/` (pure pandas, no engine) — the decomposition,
   the tail study, the by-year decay view. Lower stakes; nothing touches `strategy/` or `orders/`.

OUT OF SCOPE for the handoff: no strategy code, no engine changes until the fill-semantics question is
answered, no short-intraday leg, no parameter optimization.

---

## Changelog
- _2026-06-19_ — Initial draft. Status: Step 1 — Framing. Successor to the killed intraday-conditional
  candidate.
- _2026-06-19_ — **Step 2 complete.** Open Decisions 1–4, 7, 8 resolved (universe=SPY; basis=TRADES
  + IBKR-derived dividend overlay, deviation documented in `build_dividend_calendar.py`; source=IBKR
  daily, depth=1993-01-29 inception; IS=2006-01-01→2018-12-31 / OOS=2019-01-01→present committed;
  long-overnight-only; cash convention locked, reusing spy_short_reversal's TB3MS series). SPY daily
  TRADES loaded (8402 bars); fixed a stale-cache corruption (2 bad 2024 bars that `INSERT OR IGNORE`
  had shadowed) by deleting + re-pulling clean. Built `research/killed/overnight_drift/` package
  (`build_dividend_calendar.py`, `spy_dividends.csv`, `README.md`). QC passed: no split
  discontinuities, datetime normalized, dividend ex-dates 100% match an external reference over
  2021–2026 (amounts ±$0.03 rounding), OHLC penny-exact vs external (recent) and matching famous
  historical closes (2018-12-24=234.34, 2020-03-23=222.95). Two IBKR daily gaps documented
  (2004-07-12, 2007-07-02). Deployment fraction, execution model, and fill-semantics remain OPEN.
- _2026-06-19_ — **IS start moved 1993-01-29 → 2006-01-01** (Open Decision #4 refined). Deliberate
  pre-GFC exclusion, not a data limitation: the full 1993→present series stays cached in
  `market_data_bars` (no re-pull); 2006-01-01 is the IS-start *usage* cut only. Rationale: (1) the
  IBKR-derived dividend overlay is complete 2006+, so the entire IS is dividend-corrected — retires
  the "1997–2005 ex-div uncorrected" caveat (now moot; removed from the Step-3 carry-forward notes in
  both Open Decision #2 and the Step-2 readout); (2) pre-2006 NYSE auction microstructure (pre-Hybrid
  Market / Reg NMS) is less representative of the current opening-print mechanics the signal depends
  on. OOS boundary (2019-01-01 → present) unchanged, still no iteration. Data-gap status: 2004-07-12
  now pre-IS (irrelevant); 2007-07-02 still in-IS (Step-3 drop-both-overnights handling stands).
  No EDA, no overnight-return computation, no code.
- _2026-06-19_ — **Step 3 complete → STOPPED.** EDA in `research/killed/overnight_drift/eda.py` (3268
  IS trading days, 50 IS ex-div dates, 4 plots). Two corrections applied vs the original Step 3
  spec: (1) the tail study's "Feb–Mar 2020" reference was an OOS leak (2019+) — replaced with IS
  clusters (2008 GFC, 2011 Aug, 2015 Aug, 2018 Feb/Dec); (2) added a net-of-cost overlay to the
  decision comparison (overnight trades ~252 RT/yr vs B&H ~0 → gross comparison flatters overnight
  by ~250 bp/yr). **Result: gross overnight Sharpe 0.50 beats B&H 0.41, but net-of-cost (1 bp RT)
  overnight Sharpe 0.27 does NOT beat B&H 0.41.** The failure is structural cost asymmetry (the
  per-trade edge is too thin for 252 round-trips/year), not decay (edge is stable across IS years)
  and not tail risk (all deployment fractions survive all IS clusters above $35k backstop).
  Candidate stopped. OOS never opened. No strategy/engine/fill-semantics code written.