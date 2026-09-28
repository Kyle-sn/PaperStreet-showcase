# RESEARCH_WORKFLOW — Petroleum Status Drift (Crude / MCL)

**Status: PARKED at Step 2 (2026-08-25).** Step 1 (Framing) — LOCKED. Step 2 (data acquisition) —
DONE. **G0 FAILS** (101 usable events vs. the 200 floor) on this account's actual usable CL depth
— see Step 2 results. Per the pre-committed G0 rule: do not proceed to Step 3/backtest. Parked
rather than killed — H1 was never tested, the block is a data-entitlement depth problem, not a
rejected hypothesis. Steps 3–9 framed, not opened. Concrete unblock: a NYMEX-energy historical-data
subscription upgrade (or alternate vendor) to clear real CL depth back past the ~200-event G0 floor.
Package: `research/killed/petroleum_status_drift/` (see its `README.md` for a short data-readout summary).
**Candidate class:** Scheduled-event-anchored drift. Mechanism = structural (hedging /
physical-flow repositioning) + a behavioral (under-reaction) component.
**Instrument:** Research on **full-size CL** continuous/front series; **execute on MCL**
(Micro WTI, 100 bbl, 1/10 CL) at the $50k track. MCL settles to CL, so the signal is identical;
the micro is purely a sizing/execution choice.
**Capital track:** Default $50k / $25k PDT floor. (This is a futures strategy — no PDT rule
applies to futures — but the drawdown-budget framing still governs sizing; see Step 8.)

---

## Step 1 — Framing (LOCKED)

### Hypothesis

**H1.** Following the EIA Weekly Petroleum Status Report (crude oil inventory release,
Wednesdays 10:30 ET), the *surprise* component of the crude inventory change predicts a
**multi-day drift** in front-month WTI, in the intuitive direction: a larger-than-expected
**draw** (bullish) is followed by continued upward drift; a larger-than-expected **build**
(bearish) by downward drift. The drift is measurable **after** the instantaneous 10:30 reaction
is already in the price, and decays over roughly **1–5 trading days** — i.e. it lives inside the
2–15 day band as an output of the mechanism, not as a chosen parameter.

### Economic rationale (why a drift should exist at all)

The instantaneous price jump at 10:30 is arbitraged in milliseconds by latency-sensitive
participants. That jump is **not** the edge and must be explicitly excluded from the design
(entry is post-reaction). The candidate edge is the *residual* drift, and there are two distinct
mechanisms that would produce it:

1. **Structural / hedging-flow (primary).** Commercial participants — producers, refiners,
   merchants — do not fully reposition their hedge books on the print. Physical-flow decisions
   (refinery run adjustments, producer hedge-ratio changes, inventory financing) play out over
   subsequent days because the underlying decisions are operational, not quote-speed. This flow
   is directional and predictable in sign given the surprise, and it is a **risk-transfer /
   hedging-pressure** effect — it does **not** depend on anyone's mistake, so it is not obligated
   to decay on publication.
2. **Behavioral under-reaction (secondary).** Classic post-announcement drift: information
   diffuses into price gradually when attention and analysis are bounded. This component **is**
   subject to post-publication decay and must be tested for it empirically (Step 5 regime split).

Because the strategy mixes a decay-immune structural mechanism with a decay-prone behavioral one,
the **empirical decay test is mandatory**, not optional: split the sample by era and check whether
effect size is shrinking. A shrinking-but-nonzero effect is consistent with (1) surviving while
(2) erodes — still potentially fundable. A fully-decayed-to-zero effect is a kill.

### Why this fits the system

- **Instrument is native to the $50k track.** MCL notional (~$7k/contract) means 1–3 contracts
  is correct sizing; capacity is a non-issue at deployed capital and comfortably above it.
- **Event count, not bar count, drives sample power.** ~52 releases/year sidesteps the
  daily-bar continuous-series sample-power problem that constrained equity candidates.
- **Clean daily-bar fit.** The 10:30 reaction is contained in the report-day daily bar (CL
  settles 14:30 ET). Entering at the *next session's open* cleanly excludes the jump and captures
  only the residual drift — no intraday infrastructure required.
- **Futures dodge the equity structural headwinds** that killed three prior candidates: no PDT
  floor, clean shorts (no borrow/locate), symmetric long/short.

### Anti-pivot boundary

If H1 fails at the Step 3 event study (drift curve is not same-signed and significant
in-sample), the candidate **stops**. It does **not** mutate into "maybe distillate stocks
predict crude" or "maybe the API print the night before works instead" — those are different
hypotheses that earn their own workflow docs. Prior CL exposure in the killed TSMOM candidate does
**not** exile crude here: that was a monthly-scale trend-premium mechanism at $5M; this is a
daily-scale event-drift mechanism at $50k — economically distinct, fresh gates, so no anti-pivot
violation.

### Expected performance (calibrated to published work + your target band)

- **Sharpe.** Commodity announcement-drift effects in the literature are **modest and
  contested**. Realistic prior if the effect is real and not fully decayed: **net 0.3–0.6**.
  There is a material probability this is already arbed toward ~0 and becomes a documented kill —
  that is an acceptable and expected outcome of the process, not a failure of it.
- **Red-line.** IS net Sharpe **> 1.5 → treat as a bug or overfit**, halt and audit. Liquid-crude
  event studies do not produce large Sharpes honestly.
- **Drawdown.** Event strategies flat-between-events have shallow but frequent drawdowns;
  target max DD **< 15%** given the $25k-headroom mandate. A single bad regime (e.g. a
  surprise-sign-inverting shock) is the main tail.
- **Turnover.** ~52 round-trips/year (one entry per release, held days). Moderate. Per the cost
  principle, the optimization target is trade *count*, not size — and this count is fixed by the
  release calendar, which is a feature: it caps turnover structurally.

### Pre-committed kill criteria (frozen before any data is touched)

| Gate | Location | Pass condition | On fail |
|---|---|---|---|
| **G0 — Data depth** | Step 2 | ≥ **200 total** usable events after cleaning | Do not proceed to backtest; run IS-only exploratory or park |
| **G1 — Event-study EDA** | Step 3 | Cumulative drift over days 1–5 is **same-signed** across the window AND day-5 cumulative-drift **t ≥ 2.0 in-sample** | **Kill at EDA. No backtest.** (as `intraday_conditional` died) |
| **G2 — Beats unconditional null** | Step 4 | Conditional (surprise-gated) IS Sharpe beats the **unconditional event null** (enter every release, ignore surprise) by a margin that survives the deflated-Sharpe trial count | Kill — the surprise variable adds nothing |
| **G3 — Cost survival** | Step 6 | Net-of-cost drift remains positive after full MCL commission + spread + slippage | Kill — structural cost asymmetry (as `overnight_drift` died) |
| **G4 — IS sanity** | Step 4 | IS net Sharpe **≤ 1.5** | Halt, audit for lookahead/bug before continuing |
| **G5 — OOS one-shot** | Step 7 | Net OOS Sharpe in **0.4–0.7 fundable band**, max DD **< 20%**, and still beats the unconditional null OOS | Park with reason recorded (as `spy_short_reversal` parked) |
| **G6 — OOS event floor** | Step 7 | ≥ **60 OOS events** or OOS is too thin to open as a one-shot | If < 60: OOS is uninformative; do not spend it — either accept IS-only "effect exists" screen or park |

**Derivation of the event floors (G0, G6).** For an event-level mean drift with per-event
information ratio *IR*, the t-stat on cumulative drift ≈ *IR* · √N. To reach t ≈ 2 on a
plausible *IR* ≈ 0.15 requires N ≈ (2/0.15)² ≈ 180 events. Round to **200** for G0 to leave
headroom. The OOS one-shot needs enough events that a null result is not pure noise; **60**
(~1.15 years of releases) is the minimum at which an OOS Sharpe estimate carries any signal.
Below it, the OOS is worth more preserved than spent.

### Open Decisions (fresh list — resolve before the step that consumes each)

| # | Decision | Recommendation | Status | Blocks |
|---|---|---|---|---|
| 1 | **Surprise variable definition.** Consensus data (Bloomberg/Reuters survey) is paid and you've cut external data spend. | **Build the expectation model from EIA's own FREE historical series** (api.eia.gov, weekly crude stocks back decades): expected change = seasonal + short trend model fit on an **expanding, point-in-time** window; surprise = actual − expected, standardized by trailing surprise vol. Pre-register this as the **single primary** surprise definition; raw-change and simple-YoY-deviation are robustness-only, and every alternative counts against the trial ledger. | **OPEN — must resolve before Step 3** | Step 3 |
| 2 | **IBKR CL historical depth.** Your own TSMOM Open Decision #3 recorded IBKR futures depth at 2.5–8y. | Step 2 opens with a **depth probe** (walk IBKR back on CL front/continuous, exactly like the QQQ 5-min probe). Commit the IS/OOS boundary as a **rule now, a date later**: OOS = most recent ~30% of available events by date; boundary fixed once depth is known and **before any signal computation**. Gate on G0/G6 event floors. | **RESOLVED, gate FAILS.** CONTFUT nominally spans 2018-01-24 → present (2,116 daily bars) but 73% of that (everything before 2024-09-17) is degenerate placeholder data, not real prices — see Step 2 results. Real usable CL depth on this account is **2024-09-17 → present, ~23 months, 101 EIA events** — below the G0 floor of 200. IS/OOS split moot until G0 clears. | Step 2 ✓ (gate FAILS — see kill-criteria note above) |
| 3 | **Continuous-contract vs front-only.** A days-long hold spanning a roll books spurious roll-yield PnL. | **Front-contract only**, PnL in **dollars/contract** (not % returns — this also sidesteps the April-2020 negative-settle div-by-zero). Pre-commit a deterministic roll (e.g. roll ~N days before expiry) and **exclude any event whose 5-day holding window would cross the roll**. Avoids needing a full ratio-adjusted continuous series à la TSMOM. | **OPEN — recommend front-only** | Step 3, Step 4 |
| 4 | **Entry timing.** Report-day settle vs next-session open. | **Next-session open.** Unambiguously post-reaction; removes any question of whether the report-day settle partially straddles the 10:30 jump. | **OPEN — recommend next open** | Step 3 |
| 5 | **Holding-window as output, not search.** | The drift **decay curve (days 1..7)** is the primary EDA output. Do **not** search for the best-performing hold length and report it — pre-commit that the tradeable signal is defined over **days 1–5**, and report the whole curve. Choosing the max-Sharpe window ex-post is the canonical event-study overfit. | **OPEN — recommend days 1–5 fixed** | Step 3 |
| 6 | **Surprise threshold / conditioning.** What counts as a "large" surprise. | Pre-declare **fixed quantile cutoffs** (e.g. top/bottom tercile of standardized surprise, or \|z\| > 1), symmetric long/short. No searching over thresholds; each declared level counts against the trial ledger. | **OPEN — resolve before Step 3** | Step 3 |
| 7 | **Compliance: energy-complex adjacency.** Your role at a power exchange + the documented MNPI boundary. | **Not mine to adjudicate.** Flagged as a **pre-live** open item: confirm with your compliance function that systematically trading crude/energy futures is clear given your position and any nonpublic energy-market information access. Cheap to check now, expensive to discover at Step 9. | **OPEN — clear before Step 8/9** | Step 8, Step 9 |

---

## Step 2 — Universe & Data (framed)

- **Universe:** CL (research), MCL (execution). Single instrument. No basket.
- **Price data:** IBKR historical, front-month CL. **Open with a depth probe** (Open Decision #2)
  before committing the IS/OOS boundary. Cache to `market_data_bars` per `DATA_MODEL.md`;
  no live pulls during iteration.
- **Event data:** EIA Weekly Petroleum Status Report — crude inventory change, **free** via
  api.eia.gov, historical to well before any IBKR price depth. Pull actuals; build the
  point-in-time expectation model (Open Decision #1) here.
- **Release calendar:** must be **point-in-time correct** — EIA shifts the release to Thursday
  on holiday weeks. A mis-aligned calendar silently corrupts every event window. Build/verify the
  actual historical release-date series, don't assume "every Wednesday."
- **Contract specs to CONFIRM (do not assume):** MCL commission/side, tick size, min tick value,
  and typical bid-ask spread from IBKR/CME — needed for the G3 cost model. *Flagged rather than
  fabricated.*
- **Data basis:** futures — no dividend adjustment. Work in **dollar PnL per contract**.

### Step 2 results (2026-08-24/25, Claude Code — data acquisition only, no signal work)

**G0: FAIL.** 101 usable events vs. the 200 floor, once the price data is checked for what's
actually real (see below — the naive read looked like a PASS at 448 and was wrong). Per the
pre-committed rule: **do not proceed to Step 3/backtest.** Full detail:
`research/killed/petroleum_status_drift/` (code) + `research/killed/petroleum_status_drift/mcl_contract_specs.md`
(specs).

- **IBKR CL depth (Open Decision #2 — RESOLVED, and the depth is much shallower than the raw
  date range suggests).** A CONTFUT max-duration pull returned bars nominally spanning
  **2018-01-24 → present** (2,116 daily bars, cached to `market_data_bars`: symbol=CL,
  sec_type=FUT, bar_size=1 day, what_to_show=TRADES) — consistent with the ~2.5–8y prior recorded
  from the TSMOM research, so this initially read as a clean G0 PASS at 448 events. **It wasn't.**
  Spot-checking the bars turned up 1,550 of 2,116 (73%, everything before 2024-09-17) as
  degenerate placeholders — `open == high == low == close`, `volume == 0` — not real prices.
  Confirmed by checking 2020-04-20, the day front-month WTI famously settled negative: the cached
  bar reads a flat, positive **$38.83**. Real, dense, non-degenerate data only starts
  **2024-09-17** (486/486 bars clean through 2026-08-24). Recomputing G0 against that actual
  window drops the usable-event count from 448 to **101** — the gate fails. Root cause not
  confirmed but most likely a missing NYMEX energy historical-data entitlement on this account,
  with IBKR silently backfilling a stale-snapshot placeholder instead of erroring; documented as a
  general gotcha in `docs/IBKR_NOTES.md` since this could silently corrupt any future futures
  pull that only checks the date range and not the bar contents.
  Separately (and now moot for sourcing, since CONTFUT's *real* depth is shallower than hoped
  either way): CONTFUT flatly rejects `endDateTime` (IBKR error 10339), so it can't be paginated
  backward the way the QQQ probe pages an STK series — its depth is whatever one max-duration
  request returns. A live cross-check of individual dated FUT contracts
  (`lastTradeDateOrContractMonth`, symbol+month lookup) found they only resolve for roughly the
  trailing 12–13 months on this account regardless (2001/2008/2014/2018/2021/2023/2024/2025-01
  through -09 all came back "no security definition"; 2025-10 through 2026-03 resolved) — a
  *shorter* reachable window than even CONTFUT's nominal range, so per-contract stitching isn't a
  path to more real history here either without `reqContractDetails`-based conId resolution
  (not attempted — out of scope for a raw-acquisition pass, and unlikely to out-run the
  entitlement wall CONTFUT itself hit).
  Code: `research/killed/petroleum_status_drift/ibkr_depth_probe.py` (`probe` / `fetch` / `report` —
  `report` filters degenerate bars before counting; see `_flat_bar_mask`).
  **Action item:** a data subscription upgrade (or a different account/vendor with real NYMEX
  energy history) is the concrete next step if this candidate is worth reviving — 101 events over
  23 months is not close to 200; it needs deeper history, not a bigger cache of the same window.
- **EIA event data + release calendar (Open Decision #1's data half — RESOLVED; the
  expectation-model half of OD#1 is still open, Desktop's to build).** Full WCESTUS1 history
  pulled via api.eia.gov v2 (free, no auth beyond an API key — DEMO_KEY sufficed for the one-shot
  pull under 5,000 rows): 2,290 weekly releases, 1982-08-20 → 2026-08-14, cached to
  `research/killed/petroleum_status_drift/data/eia_weekly_crude_stocks.csv` (raw API responses archived
  under `data/raw/`). EIA does **not** publish a machine-readable historical release-timestamp
  archive, so `release_date` is **rule-derived** (Wed after period-end; Thu if that Wednesday or
  the preceding Monday is a federal holiday; Fri override for the Christmas-Day-on-Wednesday
  case), validated 14/14 against the one primary source that exists — EIA's own 2024–2025
  holiday-shift exception table. 295/2290 rows are non-standard-Wednesday releases. Known
  unmodeled irregularities (shutdowns, weather delays) flagged in the code, not silently ignored.
  Code: `research/killed/petroleum_status_drift/eia_fetch.py` (`fetch` / `validate`).
- **MCL contract specs (RESOLVED for the confirmable fields; two fields still open).** Contract
  size, tick size ($0.01/bbl), tick value ($1.00/contract), and settlement type CONFIRMED via
  cross-corroborated secondary sources (CME's and IBKR's own pages both blocked automated
  fetches this session — 403/timeout). Commission (~$0.25/contract/side + exchange fees, third-
  party-sourced) is CORROBORATED but not primary-fetched. **Typical bid-ask spread is
  UNRESOLVED** — it's a live microstructure property, not a spec-page fact, and `IBApp` doesn't
  currently implement the `tickPrice`/`contractDetails` EWrapper callbacks needed to sample it
  live; adding those is a change to shared production infra and was left out of this session's
  scope rather than rushed. Both open items block G3 and are the concrete next step for whoever
  picks up Step 2 follow-up (live commission via a what-if order, live spread via a quote sample
  — both need `IBApp` additions). Full sourcing/confidence table:
  `research/killed/petroleum_status_drift/mcl_contract_specs.md`.

## Step 3 — Signal / event study (notebook, IS only)

Point-in-time surprise (OD#1) → align to point-in-time release calendar → enter next open (OD#4)
→ measure cumulative dollar drift days 1..7 (OD#5), conditioned on surprise quantile (OD#6),
symmetric long/short. **Primary output: the decay curve + G1 significance test.** Kill at EDA
if G1 fails. Report the deflated Sharpe carrying the OD#1/OD#5/OD#6 trial count.

## Step 4 — IS backtest (through the engine)

Route the frozen signal through PaperStreet's `BacktestEngine`/`SimBroker` (event-loop, not
vectorized). Benchmark = **unconditional event null** (G2), not buy-and-hold crude (crude B&H is
the wrong baseline for a flat-between-events strategy). Enforce G4 IS-sanity ceiling.

## Step 5 — Parameter sensitivity + mandatory regime segmentation

Report across eras — **2014–16 crash, 2017–19 range, 2020 COVID/negative, 2021–22 spike,
2023+ normalization** — not a single pooled Sharpe (the TSMOM drought-attribution lesson). This
is also where the **decay test** lives: is effect size monotonically shrinking toward the recent
era? Sensitivity to the OD#6 threshold and OD#3 roll rule reported, not re-optimized.

## Step 6 — Cost stress (G3)

Full MCL round-trip: confirmed commission + spread + conservative slippage. Net drift must stay
positive. Since cost scales with trade count and this strategy's count is calendar-fixed at ~52/yr,
the cost drag is bounded and estimable up front.

## Step 7 — OOS one-shot (G5, G6)

Frozen spec, run once on the reserved recent-era events. No re-tuning. Beats unconditional null
OOS + fundable band + DD gate. Respect the G6 event floor — a thin OOS is worth more preserved.

## Step 8 — Paper trade

Live MCL fills vs backtest assumptions; release-day timing in real conditions (does the next-open
entry fill where the backtest assumed?); roll handling live. **Compliance clearance (OD#7)
resolved before this stage.**

## Step 9 — Pre-live checklist → live small

Standard `RISK.md` gate (order-size cap, kill switch, stale-data guard, drawdown-budget monitor).
Size against the drawdown budget with $25k headroom. **OD#7 compliance sign-off is a hard blocker.**

---

## Ways this backtest will lie to me

1. **Capturing the 10:30 jump.** If entry is modeled at/inside the report-day reaction, the
   backtest books a jump the live daily-bar system can never get. → Entry at **next open**, always.
2. **Lookahead in the expectation model.** Fitting the seasonal/trend surprise model on the full
   sample (including future) fabricates surprise skill. → **Expanding, point-in-time** fit only.
3. **Roll contamination.** A contract roll inside the holding window books spurious roll-yield as
   alpha. → Front-only, dollar PnL, **exclude events whose window crosses the roll** (OD#3).
4. **Mis-aligned release calendar.** Assuming "every Wednesday" mis-dates holiday-week releases and
   scrambles every window. → **Point-in-time actual release dates.**
5. **April 2020 negative settle.** % returns explode; naive fills at impossible prices. → Dollar
   PnL space + a pre-committed handling rule for the negative-print window.
6. **Ex-post window selection.** Reporting the best-Sharpe hold length. → Report the **whole decay
   curve**; tradeable window pre-fixed at days 1–5 (OD#5).
7. **Regime-artifact Sharpe.** A pooled Sharpe driven entirely by one crude regime (e.g. the 2020
   dislocation). → Mandatory per-era segmentation (Step 5); anchor priors on the boring range-bound
   era, not the dislocations.
8. **Multiple testing.** Surprise-def × threshold × window is a real free-parameter count for an
   event study. → Pre-declare all, carry the **deflated Sharpe** with full trial count.

---

## Provenance

- Framing authored in Claude Desktop (design track). Data pull + notebook = Claude Code.
- Prior related kill: `RESEARCH_WORKFLOW_diversified_trend.md` (CL at monthly-trend/$5M — distinct
  mechanism, does not exile this candidate).