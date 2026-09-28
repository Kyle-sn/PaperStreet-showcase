# Research Workflow — Intraday Conditional Strategy on QQQ

> **STATUS: STOPPED at Step 3 (2026-06-19).** H1 was not supported in-sample: the conditional
> cross-tab shows no monotonic structure and no statistically significant bucket, the only
> suggestive tail effect is overnight-gap momentum, and the tail sign inverts at the 30/90-min
> windows. Per the workflow's anti-pattern rule the project stops — no re-bucketing, no hypothesis
> pivot, and **OOS (2022–2025) is never opened**. See the Step 3 verdict below. The diversified-trend
> strategy (`research/killed/research_notes/RESEARCH_WORKFLOW_diversified_trend.md`) has been resumed as
> ACTIVE (2026-06-19).

Research plan for an intraday strategy that uses the first hour of US equity trading as a
signal and expresses the resulting trade in the remainder of the session. Follows the same
step structure as `research/killed/research_notes/RESEARCH_WORKFLOW_diversified_trend.md`.

---

## Status

**Step 3 — Signal EDA (COMPLETE → STOP).** Pure EDA on IS (2015–2021) QQQ 5-min bars; OOS
untouched. The H1 conditional structure is absent: non-monotonic cross-tab, every bucket |t| < 1.3,
gap-momentum confound, and tail-sign inversion across the 30/60/90-min windows. **Project stopped at
Step 3.** Code + plots in `research/killed/intraday_conditional/` (`eda.py`, `notebook.ipynb`, `plots/`).
Full verdict in the Step 3 section below.

**Step 2 — Universe and Data (COMPLETE).** 224,552 QQQ 5-min bars loaded into
`market_data_bars` via IBKR (sole source — probe confirmed data back to 2014-01).
Quality checks passed: 2,863 full days, 24 half-days identified and exported, 4
incomplete-session IBKR gaps labeled, DST correct across all 23 transition dates.

---

## Strategy Summary

One sentence: **after the first 60 minutes of the regular session, take a position in QQQ
for the remainder of the day, where the direction and sizing are conditioned on what
happened in that first hour.**

**Universe: QQQ only.** SPY dropped — it is ~0.9 correlated with QQQ so running both is
nearly the same trade twice; QQQ has more post-open dispersion historically. No SPY, no
single names, no futures, no earnings calendar.

The first hour is used purely as a signal — no trades are placed before 10:30 ET. The trade
is held into the close and exited on a time-only rule: the exit signal fires one bar early so
the `next_open` fill lands at ~15:55 ET (flat into close). The position is always flat
overnight.

---

## Hypothesis

**H1 (primary).** The first-hour return `r_1h = (close_10:30 − open_9:30) / open_9:30` is
predictive of the rest-of-day return `r_rod = (close_15:55 − close_10:30) / close_10:30`,
with a relationship that flips sign depending on the *magnitude* of `r_1h` normalized by
recent realized volatility.

**Economic rationale.** Two distinct microstructure regimes drive the open:
- **Small first-hour moves** (`|r_1h|` below ~0.5σ of trailing intraday ATR): the open is
  digesting overnight noise. Mean reversion to the open / VWAP is the dominant flow as
  liquidity normalizes through the day. Expression: **fade the first-hour move.**
- **Large first-hour moves** (`|r_1h|` above ~1.5σ): the open is repricing real information
  (overnight news, macro, earnings spillover). Trend continuation as additional information
  is absorbed and slower participants enter. Expression: **continue with the first-hour
  direction.**
- The middle bucket is the danger zone — neither effect dominates and it should likely be
  no-trade. Confirming or rejecting this is part of Step 3.

**What I expect to find if this works.** Modest edge, gross Sharpe in the 0.8–1.5 range on
IS, dropping meaningfully on OOS and cost-stress. Worse on SPY than QQQ (QQQ has more
post-open dispersion historically). Strongly regime-dependent — should look very different
in 2020, 2022, vs 2017 or 2024.

**What would falsify the hypothesis.** No conditional separation between regimes in EDA
(Step 3) — i.e., if `r_rod | r_1h` looks unconditionally random across all `|r_1h|`
buckets, there is nothing to trade and we stop at Step 3.

---

## Kill Criteria (committed before any backtest)

These are committed now, before looking at IS data. Any one of them triggers a stop.

| Criterion | Threshold | Where evaluated |
|---|---|---|
| Net Sharpe (IS, costs included) | < 1.0 | Step 4 |
| Max drawdown (IS) | > 15% of allocated capital | Step 4 |
| Profit factor (IS) | < 1.3 | Step 4 |
| Parameter sensitivity — best vs median Sharpe | best > 2× median | Step 5 (overfit signal) |
| Cost-stressed (2× costs + 2× slippage) Sharpe | < 0.5 | Step 6 |
| OOS Sharpe vs IS Sharpe | OOS < 50% of IS | Step 7 |
| OOS max drawdown | > 15% of allocated capital | Step 7 |
| Absolute live-trough backstop | projected live trough < $35k | Step 7 |
| Paper-trade tracking error vs backtest | > 50bps/trade unexplained | Step 8 |

**Drawdown gates.** Both IS and OOS max-DD are 15% of allocated capital. The absolute
backstop ($35k) sits $10k above the $25k PDT floor — if projected drawdown would breach
it, the strategy is dead regardless of percentage.

---

## Open Decisions

Tracked here until resolved. Each should be closed before the step that depends on it.

### Resolved

- [x] **Bar size → 5-min.** Enough resolution for an open→close strategy; much cheaper
      to store and process than 1-min (~78 bars/day vs 390). Locked before Step 2.
- [x] **Intraday data source → IBKR (sole source).** Probe confirmed QQQ 5-min bars
      reachable back to 2014-01-02 — well past the 2015-01 target. No external source
      needed. 224,552 bars fetched in 138 monthly chunks, auto-upserted to
      `market_data_bars`. CSV loader built as fallback but unused.
- [x] **IS / OOS split → committed before looking.** IS = 2015-01-01 through 2021-12-31
      (7 years, includes 2018 vol, COVID). OOS = 2022-01-01 through 2025-12-31 (4 years,
      includes 2022 bear, 2023 recovery, 2024 low-vol, 2025 tariff vol). **No iteration on
      OOS, period.** This is a usage split, not a pull filter — the full 2015→present
      window is loaded.
- [x] **Exit rule → time-only, one bar early.** The exit signal fires on the 14:50 CT bar
      (= 15:50 ET), and `next_open` fill lands at the 14:55 CT bar open ≈ 15:55 ET. Flat
      into close. No profit target, no stop, no VWAP cross — no extra parameters.
- [x] **Half-day sessions → dropped and labeled.** The strategy's exit assumes a full
      session; half-days (1 PM ET close) are identified empirically from bar counts (42
      bars vs 78), exported to `research/killed/intraday_conditional/half_days.json`, and excluded
      in both backtest and live. Identification is a quality-check output
      (`quality_checks.py`).
- [x] **Universe → QQQ only.** SPY dropped (~0.9 correlated, nearly the same trade twice;
      QQQ has more post-open dispersion). No single names, futures, or earnings calendar.

### Closed / moot after the Step 3 STOP

The project stopped at Step 3, so the remaining "lock before Step 4/8" decisions are moot. Recorded
for the record:

- [x] **First-hour window length.** Checked 30/60/90 in Step 3 EDA (output 6). The tail signal
      *inverts* across windows — there is no robust window, which is part of why H1 was rejected.
- [~] **Position sizing rule.** (a) fixed dollar notional vs (b) Kelly-fractional — never reached;
      no edge to size.
- [~] **Cost model.** A ~1.37 bps round-trip floor (1.0 slippage + 0.37 commission at $50k) was used
      for the Step-3 cost overlay only; never locked for a backtest.
- [~] **PDT mitigation.** Never reached (no strategy to take live).

---

## Workflow

### Step 1 — Framing

**Objective.** State the hypothesis, the economic rationale, expected return profile, and
the conditions under which the work stops. (This document.)

**Done when.** Hypothesis, kill criteria, and open decisions list are written down and
have not changed for 24 hours of reflection. Resist the urge to start pulling data before
this step is locked.

**Anti-pattern to avoid.** Generalizing the hypothesis when EDA disappoints. If H1 fails
in Step 3, the project stops. It does not pivot to "well, maybe overnight gap predicts
intraday range instead" — that's a different strategy and deserves its own workflow doc.

---

### Step 2 — Universe and Data

**Universe.** QQQ only. No SPY (dropped — ~0.9 correlated, see Resolved Decisions above),
no single names, no futures.

**Data needed.**
- 5-min bars for QQQ, spanning the full pull window (2015-01-01 → most recent complete
  trading day). The IS/OOS split is a usage decision applied at analysis time, not a pull
  filter.
- Daily bars for QQQ (already accessible via IBKR for trailing ATR / realized vol features).
- Overnight close-to-open returns (derivable from daily bars).

**Data basis.** `TRADES` (split-only, unadjusted). QQQ has had no splits in-window;
dividend adjustment is irrelevant for an intraday-flat-overnight strategy. Consistent with
the `what_to_show` cache-key convention so it never collides with another series.

**Data source.** IBKR (sole source). Probe confirmed QQQ 5-min bars back to 2014-01-02.
224,552 bars fetched in 138 monthly chunks. Free, and backtest/live share one source.

**Implementation.** `research/killed/intraday_conditional/`:
- `fetch_5min_bars.py probe` — walks IBKR backward to find the earliest reachable date
- `fetch_5min_bars.py fetch` — pulls monthly chunks from IBKR into `market_data_bars`
- `fetch_5min_bars.py load_csv PATH` — loads external CSV (FirstRate/Polygon format)
- `quality_checks.py` — runs all quality checks on loaded data

**Storage.** Goes into the existing `market_data_bars` table per `DATA_MODEL.md`. UNIQUE
key is `(symbol, sec_type, bar_size, bar_datetime, what_to_show)`. Bar datetimes are
normalized to naive ISO by `_normalize_bar_datetime` (timestamps in TWS display timezone,
currently US/Central).

**Quality checks before any analysis.**
- No gaps within RTH on non-holiday trading days (78 bars per full day).
- Half-day sessions (1pm ET close) identified empirically from bar counts (42 bars),
  labeled, and exported to `half_days.json` for filtering.
- DST transitions produce consistent first-bar times (08:30 CT year-round).
- Spot-check 5-10 random days' daily OHLC (derived from 5-min bars) vs Yahoo Finance.
- If external source used: cross-source parity check over the IBKR overlap window — OHLC
  must agree within a few bps; investigate any systematic difference.

**Quality check results (2026-06-18):**
- 224,552 bars, 2,891 trading days (2014-01-02 → 2026-06-18)
- 2,863 full days (78 bars), 24 half-days (42 bars), 4 anomalous (IBKR gaps)
- Half-days and incomplete days exported to `research/killed/intraday_conditional/half_days.json`
- DST: PASS — 08:30 CT first bar on all 23 transition Mondays
- Anomalous days (late open, partial session): 2017-01-05, 2018-09-17, 2019-08-05,
  2019-09-13 — all in IS window. Drop alongside half-days.
- Yahoo spot-check: skipped (transient yfinance API error). Can re-run later.
- Cross-source parity: N/A (IBKR is sole source)

**Done.** All data is in `market_data_bars`, quality checks passed, half-days + incomplete
days exported, and this doc's resolved-decisions list is current.

---

### Step 3 — Signal Exploration (Notebook)

**Where.** A notebook in `research/`, **not** the strategy module yet. No backtester
called here. Pure EDA.

**EDA outputs required (all on IS data only):**

1. **Intraday vol profile.** Average realized vol per 5-min bucket across the trading day
   for QQQ, by year. Confirms the U-shape and quantifies how the open compares to midday.

2. **First-hour return distribution.** Histogram and time-series of `r_1h`, by year.
   Annualized vol of first-hour returns. Compare to overnight return distribution.

3. **The conditional cross-tab — the key test of H1.**
   - Bucket days by `|r_1h|` normalized by trailing 20-day intraday ATR — say into
     quintiles or sextiles.
   - For each bucket, compute the average and median `r_rod`, signed against `r_1h`
     (i.e., is rest-of-day in the same direction or opposite).
   - Look for monotonic structure: small `|r_1h|` → fade dominates (negative average
     signed `r_rod`); large `|r_1h|` → continuation dominates (positive average signed
     `r_rod`).
   - Compute t-stats per bucket. Be honest about multiple testing — six buckets still
     means some will look "significant" by chance.

4. **Overnight gap conditioning.** Same cross-tab but additionally conditioned on
   overnight gap sign and size. The most common version of this trade conflates
   first-hour move with overnight gap — separate them before assuming the signal is
   really the first-hour move.

5. **Regime dependence.** Repeat the conditional cross-tab year by year. If the
   relationship inverts or vanishes in any major year (2018, 2020, 2022 are the obvious
   ones), that's a serious red flag — note it now, before backtesting compounds it.

6. **Window-length robustness.** Repeat the primary cross-tab for 30-min and 90-min
   first-hour windows. If 60 is special but 30 and 90 don't show the pattern, the 60-min
   result is suspect.

**Decision point.** After EDA, before any backtesting:
- If the conditional structure in (3) is visible in **most** years, proceed to Step 4 with
  the parameters that EDA suggests (NOT the parameters that optimize EDA — pick reasonable
  round numbers in the same neighborhood).
- If the structure is visible only in some years, **document it honestly here** and decide
  whether the failing years represent a regime likely to recur, or stop.
- If no structure is visible, stop. Do not search further until something works.

**Done when.** The decision above is written down in this doc with the supporting plots
saved alongside the notebook.

---

### Step 3 — VERDICT (2026-06-19): **STOP**

Pure EDA, IS-only (2015-01-01 → 2021-12-31, 1,733 valid full days after dropping half-days and
IBKR-gap days), OOS never loaded (SQL hard-bounded `< 2022-01-01`; runtime-asserted). 2014 used as
warm-up only. Code: `research/killed/intraday_conditional/eda.py` (workhorse) + `notebook.ipynb`
(narrative); plots in `research/killed/intraday_conditional/plots/`.

**Committed feature definitions (locked here for any future reuse).**
- **Bar-edge convention.** IBKR 5-min bars are START-labeled and stored in US/Central; converted to
  US/Eastern. "close at time *T*" = close of the bar **ending** at *T* = close of the *(T−5min)*
  labeled bar (matching the spec). So `open_0930` = open of the 09:30 bar; `close_1030` = close of
  the **10:25** bar; `close_1555` = close of the **15:50** bar (≈ open of the 15:55 bar = the
  flat-into-close exit fill, so the EDA measure lines up with the tradeable exit). `session_close`
  (close of the 15:55 bar = true 16:00) is used only for the *next* day's overnight gap.
- `r_1h = close_1030/open_0930 − 1` (first 60 min, 09:30→10:25); `r_30`/`r_90` end at the 09:55 /
  10:55 bar close. `r_rod = close_1555/close_1030 − 1` (10:25→15:50).
- **ATR_pit** = point-in-time trailing-20-day mean of daily `(high−low)/open` over the 20 valid days
  **strictly before** day *t* (`shift(1).rolling(20)`; no same-day, no future). `m = |r_1h|/ATR_pit`.
- `signed_rod = sign(r_1h)·r_rod` (>0 continuation, <0 fade).
- **Committed bucket scheme:** SEXTILES of `m`, edges fixed once at the 1/6..5/6 IS quantiles
  (`0.064, 0.134, 0.216, 0.325, 0.510` for the 60-min window). No threshold search; each window gets
  its own edges from its own `m`.
- **Cost floor** (Step-4 assumptions, $50k ref notional, ref price ~$189): 1.0 bps slippage RT +
  0.37 bps commission RT ≈ **1.37 bps round-trip** (Step-6 stress ~2.7 bps).

**Findings (the six outputs).**
1. **Vol profile** — classic U-shape every IS year. 09:30 bar ≈ 2.0× midday vol; the 10:25 bar
   (where the trade would enter) ≈ 1.2× midday. A 10:30 entry sits on the *shoulder* of the U, so
   the Step-6 worry about elevated open-side costs is real.
2. **r_1h distribution** — first-hour ann. vol ≈ 8.3%, **smaller** than the overnight gap ≈ 13.5%.
   The gap is the bigger driver, which is why output (4) matters.
3. **Conditional cross-tab (KEY TEST) — FAIL.** Mean signed `r_rod` by sextile is a non-monotonic
   zigzag (−6.2, +4.5, −2.3, −3.8, −2.0, +5.4 bps). The two tails *happen* to carry the
   H1-consistent signs (sextile 1 fades, sextile 6 continues) but **every |t| < 1.3** and each
   per-bucket SE (~5 bps) is as large as the nominal edge. **No bucket is distinguishable from
   zero**, so comparing means to the ~1.4 bps cost floor is moot (the "|mean| > floor" hits for all
   six buckets purely as sampling noise; |t|>2 buckets: **none**).
4. **Gap disentangling — FAIL.** No coherent structure survives a gap sign × size split. The only
   suggestive cell (sextile-6 continuation) concentrates in the **large-gap** subsets — the
   signature of overnight-gap momentum, not a first-hour-continuation edge. This is exactly the
   conflation the framing warned about.
5. **Regime dependence — FAIL.** Only sextile 1 (consistently mildly negative) and sextile 6
   (positive in 6/7 years, but flips negative in **2019**) show even weak sign-persistence, both
   tiny vs noise. Middle sextiles flip chaotically (sextile 3: …−40 bps in 2018…). No coherent
   regime; 2018/2020 scramble the middle.
6. **Window robustness — FAIL (decisive).** The 60-min tail signs (sextile 1 ≈ −6, sextile 6 ≈ +5)
   **invert** at both 30-min (≈ +3 / −3) and 90-min (≈ +1 / −4). The pattern exists *only* at 60
   minutes and reverses in the neighbouring windows — the notes' explicit "60 is special" red flag.
   A genuine microstructure effect would not vanish and flip between 30 and 90 minutes.

**Decision.** The Step-3 rule requires the conditional sign structure to be present/monotonic-ish,
survive the cost floor in the tails, not be explained by the gap, AND hold across most years and the
30/90 windows. It fails on **all** counts. **H1 is rejected in-sample → STOP.** Per the Step-1
anti-pattern note, the project does not pivot to a related hypothesis (e.g. "gap predicts intraday
range") — that would be a different strategy with its own workflow. **No backtest is run; OOS
(2022–2025) is not opened.**

---

### Step 4 — In-Sample Backtest

**Objective.** A single backtest run on IS data using the parameters chosen at the end of
Step 3, evaluated against the kill criteria.

**Strategy implementation.** As a `BaseStrategy` subclass in `strategy/` per the framework
in `STRATEGY.md`. `on_bar` handles 5-min bars. The strategy maintains:
- A `RollingWindow` of the day's bars to compute `r_1h` at 10:30
- A trailing realized-vol estimate for the `|r_1h|` normalization
- A flag for whether the day's signal has already been generated (one trade per day)
- A flag for exit time (15:55)

It emits at most one `OrderRequest` to enter and one to exit per day.

**Run via.** `backtesting/runner.py::run_backtest(BacktestConfig(...))` per
`BACKTESTING.md`. Use `fill="next_open"` (the default; lookahead-safe).

**Cost assumptions for this step.** IBKR tiered pricing: $0.0035/share, $0.35 minimum.
Slippage: 0.5 bps per side on QQQ at 10:30 entry; 0.5 bps per side at 15:55 exit. These
are realistic-but-not-conservative; the conservative version comes in Step 6.

**Outputs.** Equity curve, trade log, metrics table (per `BACKTESTING.md` — Sharpe,
max DD, win rate, total trades, total commission). Compare every metric to kill criteria.

**Done when.** Either a kill criterion triggers (stop, document, move on to a different
project) or all kill criteria are passed and we proceed to Step 5.

**Anti-pattern to avoid.** Tweaking parameters after seeing IS results and re-running.
That's the start of overfitting. If the chosen parameters fail, the answer is "this
hypothesis didn't survive contact with the data" — not "try a different window length."

---

### Step 5 — Parameter Sensitivity

**Objective.** Confirm the IS result is not perched on a parameter spike.

**Method.** Sweep each parameter independently across a reasonable neighborhood while
holding the others at the Step 4 value:
- First-hour window: 30, 45, 60, 75, 90 minutes
- Vol-normalization lookback: 10, 20, 40 days
- Entry threshold (the `|r_1h|/σ` cutoff between fade / no-trade / continue): ±25% around
  the Step 4 values
- Exit time: 15:30, 15:45, 15:55, 16:00

**Pass criterion.** The chosen parameter set's Sharpe is within ~0.3 of the median of its
neighborhood for every dimension. If any single parameter shows a sharp spike (chosen
value Sharpe > 2× median), the result is fragile and the kill criterion triggers.

**What this is not.** This is not parameter optimization. We do not adopt the best-Sharpe
parameter set found in the sweep. We confirm the originally-chosen set sits in a stable
neighborhood, then keep going with it.

**Done when.** Sensitivity plots are saved and the chosen parameter set has passed the
neighborhood-stability check, or a kill triggers.

---

### Step 6 — Cost Stress

**Objective.** Verify the strategy survives realistic-pessimistic costs.

**Method.** Re-run the Step 4 backtest with:
- 2× the Step 4 commission rate
- 2× the Step 4 slippage assumption
- An additional "open spread" penalty if the entry is at 10:30 — debatable how relevant
  this is for QQQ but worth modeling

**Pass criterion.** Net Sharpe > 0.5 (per kill criteria above).

**Why this matters here.** Even though QQQ has tight spreads, a 10:30 entry is in the
*tail* of the U-shape, not the trough. Spreads and impact are non-trivially higher at
10:30 than 12:00. Pretending otherwise is the kind of cost-model error that turns
backtests into paper profits.

**Done when.** Cost-stressed metrics are documented or kill triggers.

---

### Step 7 — Out-of-Sample (One-Shot)

**Objective.** Single, final, no-iteration evaluation on held-out data (2022–2025, locked
in Step 2).

**Rules of engagement.**
- Run exactly once with the parameters and strategy code locked from Step 6.
- No look-and-tweak. If OOS fails the kill criteria, the project stops.
- "Failure on OOS suggests a different parameter would work better" is the most dangerous
  sentence in this entire document. Do not write it.

**Done when.** OOS metrics are computed, documented, and compared to IS. Decision: proceed
to paper or kill.

---

### Step 8 — Paper Trading

**Objective.** Validate that the live system reproduces the backtested behavior under real
market data, real timing, and real broker semantics — without real capital.

**Setup.**
- Live strategy connected to TWS paper account (port 7497) via `run_live.py`
- Strategy registered and selected by `STRATEGY_NAME` per `STRATEGY.md`
- All orders flow through `orders/order_handler.py` (no `placeOrder` bypass)
- Logging captures every signal, every order, every fill — and the corresponding
  bar data used to generate the signal — so trades can be reconciled against what the
  backtest would have produced on the same bars

**Duration.** Minimum 4 weeks of live paper bars covering a mix of vol regimes. Longer if
the period is unusually quiet.

**Pass criterion.** Per-trade tracking error vs same-day backtest fills < 50 bps on
average, with no unexplained outliers. The point is operational correctness, not P&L —
4 weeks is not a statistically meaningful sample for Sharpe.

**Done when.** Paper trades reconcile to backtest within tolerance for the full window, or
operational issues are surfaced and fixed and the window restarts.

---

### Step 9 — Pre-Live Checklist

Operational items that must all be done before any live capital is connected. Cross-ref
with `RISK.md`.

- [ ] System-wide per-order share-size limit implemented in `orders/order_handler.py`
      (currently a known gap in `RISK.md`)
- [ ] Kill switch flag implemented and tested
- [ ] Daily loss limit implemented (drops new orders when realized + unrealized PnL
      breaches threshold)
- [ ] PDT mitigation in place — either equity buffer + drawdown cutoff, or trade-count
      throttle. Decided in Open Decisions.
- [ ] Account number switched from paper (`DUxxxxxxx`) to live in env config
- [ ] TWS port switched from 7497 to 7496
- [ ] Reconnect behavior verified — TWS daily auto-restart does not leave the strategy in
      an inconsistent state (currently a known gap in `IBKR_NOTES.md`)
- [ ] Stale-data guard — strategy refuses to generate signals if last bar is older than
      N seconds
- [ ] Position reconciliation between `self.positions` and IBKR's `reqPositions` at start
      of every session
- [ ] Logging and alerting verified — at minimum, an alert fires on any unexpected
      strategy exit, order rejection, or DB write failure

---

### Step 10 — Live Small

**Objective.** Run the strategy live with real capital at the smallest size that is still
informative.

**Sizing.** Smallest share size that the strategy can express (1 share if the strategy
allows, else the minimum lot). Run for a minimum of 4 weeks. Track every trade against
the backtest equivalent for the same bars.

**Scaling rule.** Scale up only after:
- 4+ weeks of live small with no operational incidents
- Live tracking error within paper-trade tolerance
- Realized Sharpe within 1σ of OOS Sharpe (do not require it to match — sample is small)

**Anti-pattern to avoid.** Scaling up because early live trades happen to be profitable.
The decision to scale is operational and statistical, not a P&L vote.

---

## Cross-References

- `STRATEGY.md` — strategy interface, `OrderRequest`, `RollingWindow`, registry pattern
- `BACKTESTING.md` — `BacktestConfig`, fill assumptions, cost handling, IS/OOS discipline
- `DATA_MODEL.md` — `market_data_bars` schema, datetime normalization
- `RISK.md` — current and missing risk controls, parameters before live
- `IBKR_NOTES.md` — historical data rate limits, reconnect gap, error codes
- `ROADMAP.md` — system-wide order limits, kill switch, reconnect are in Backlog

---

## Handoff summary for Claude Code

Step 2 infrastructure is built in `research/killed/intraday_conditional/`:
- `fetch_5min_bars.py` — IBKR probe, IBKR fetch (monthly walk-back), external CSV loader
- `quality_checks.py` — RTH gap check, half-day detection, DST check, Yahoo spot-check,
  cross-source parity

The `IBKRMarketDataClient` was extended with `end_date_time` and `timeout` parameters
(backwards-compatible defaults) to support the walk-back fetch pattern.

**Next steps (sequential):**
1. Run `probe` with TWS up → determines data source path (A or B)
2. Run `fetch` (and `load_csv` if path B) → populates `market_data_bars`
3. Run `quality_checks.py` → validates data, exports `half_days.json`
4. Hand off to Step 3 (EDA notebook in `research/`)

---

## Changelog

- 2026-06-19 — **Step 3 COMPLETE → STOP.** Pure EDA on IS (2015–2021) QQQ 5-min TRADES bars; OOS
  hard-bounded out of the load and never opened. Committed feature defs (START-labeled bar-edge
  convention, point-in-time trailing-20-day ATR, `m=|r_1h|/ATR`, signed `r_rod`) and the fixed
  sextile bucket scheme. H1 rejected: non-monotonic cross-tab with every bucket |t| < 1.3,
  gap-momentum confound, and tail-sign inversion across 30/60/90-min windows. Project stopped — no
  backtest, no pivot. Code/plots: `research/killed/intraday_conditional/{eda.py,notebook.ipynb,plots/}`,
  `README.md`. First-hour-window open decision closed; sizing/cost/PDT decisions moot.
- 2026-06-18 — **Step 2 COMPLETE.** Universe locked to QQQ only; bar size = 5-min;
  IS/OOS split committed (2015–2021 / 2022–2025); exit rule = time-only one-bar-early;
  half-days dropped+labeled; kill criteria tightened (OOS DD 20%→15%, absolute $35k
  backstop). IBKR probe: QQQ 5-min bars reachable to 2014-01. Full fetch: 224,552 bars
  in 138 monthly chunks. QC: 2,863 full days, 24 half-days, 4 IBKR-gap days, DST clean.
  `IBKRMarketDataClient` extended with `end_date_time` / `timeout`. `initialize_db`
  migration ordering fixed (column migrations before schema indexes).
- _Prior_ — Initial draft. Status: Step 1 — Framing.