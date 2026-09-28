# Overnight Drift — Step 2 Data Readout

Research-tier package for the overnight-drift candidate (long the closing→opening session, flat
intraday). Workflow + hypothesis + kill criteria:
`research/killed/research_notes/RESEARCH_WORKFLOW_overnight_drift.md`.

**Scope of this package (Step 2 only):** data load + dividend calendar + quality checks. No EDA, no
overnight-return computation, no strategy/engine code — those are gated Step 3+ handoffs.

## Contents
- `build_dividend_calendar.py` — derives the SPY ex-dividend calendar from the cached IBKR daily
  bars (reproducible, DB-only, no TWS once bars are cached). Run: `python -m research.killed.overnight_drift.build_dividend_calendar`.
- `spy_dividends.csv` — output: `ex_date, amount` (85 ex-dividends, aligned to ex-div mornings).

## Data loaded
- **SPY daily, `what_to_show='TRADES'`** → `market_data_bars` (cache key includes `what_to_show`,
  so this does not collide with the existing SPY `ADJUSTED_LAST` series).
- **8402 bars, 1993-01-29 → 2026-06-18**, stored date-only ISO (`YYYY-MM-DD`).

## Probe result (Open Decision #3)
IBKR serves SPY daily TRADES back to **1993-01-29 — SPY's listing date**, a hard floor (the ETF
did not exist before). Deeper than the doc's proposed 2004 start: dot-com top, 2008, and COVID are
all in-sample.

## Decisions locked (see the workflow doc for full text)
- Universe = **SPY only**.
- Basis = **TRADES + additive dividend overlay** (justified deviation from the house `ADJUSTED_LAST`
  convention — the ex-div drop lands at the open, exactly what the overnight return measures, and
  multiplicative back-adjustment smears it; deviation documented in `build_dividend_calendar.py`).
- Source = **IBKR daily**, full depth.
- IS = **1993-01-29 → 2018-12-31**, OOS = **2019-01-01 → present**. Committed, no OOS iteration.
- Structure = **long-overnight-only**. Cash = **excess-over-3M-T-bill**, reusing
  `research/killed/spy_short_reversal/sensitivity.py::TB3MS_ANNUAL_PCT` (single authoritative series).

## Dividend calendar — method & validation
Derived by differencing the two cached IBKR series: `div(ex) = close(ex-1) · (adj_ret − px_ret)` on
each day; the date carrying a non-zero gap **is** the ex-dividend morning. Noise floor: ADJUSTED_LAST
is 2-decimal, so a few-bps rounding hash sits on every day (all < $0.02); real SPY dividends are all
> $0.10 — a clean gap, cutoff $0.02.
- **Validation:** all **20** ex-dates over the 2021–2026 overlap match an external reference
  (stockanalysis.com) **exactly** (0 date mismatches); amounts agree within **±$0.03** (the 2-decimal
  rounding — ~0.4 bp on a ~25 bp overnight dividend return, immaterial).
- **Coverage caveat (Open Decision #2):** IBKR's ADJUSTED_LAST only encodes SPY dividends from
  ~2006-03 on (ratio frozen 1997–2005). So the calendar is complete 2006+, **empty 1997–2005**,
  sparse 1993–1996. No external pre-2006 source for v1. Step 3 must flag the uncorrected ex-div
  artifact (~−20–30 bps on ~4 mornings/yr) in 1997–2005.
- The latest entry (2026-06-18, ~$1.32) is boundary-affected: IBKR smeared the most-recent ex-date
  across 06-17/06-18 (a paired −1.31 / +1.32); the positive filter keeps the real +1.32. Provisional;
  re-pull will firm it.

## Quality checks (all pass)
1. **No missing trading days vs NYSE calendar** — only **2** genuine gaps over 33 years:
   **2004-07-12** and **2007-07-02** (real sessions, confirmed unrecoverable from IBKR via anchored
   re-requests). All other apparent discrepancies were the check's own calendar being too strict
   (pre-1998 MLK days — NYSE began observing MLK in 1998; and Dec-31s where Jan 1 fell on a Saturday,
   which NYSE does not take off). **Action for Step 3:** drop overnight returns spanning the 2 gaps.
2. **No residual split discontinuities** — 0 days with |daily return| > 16% (TRADES is split-adjusted
   by IBKR; nothing further needed).
3. **Ex-div alignment** — validated by the external cross-check above (the naive "open drops ~div
   below prior close" check is confounded: the overnight *market* move usually dwarfs the ~25 bp
   dividend, so it cannot isolate the dividend from open data alone; the close-to-close derivation +
   external date/amount match is the rigorous test).
4. **OHLC spot-check vs external** — recent closes penny-exact (e.g. 2026-06-18=746.74,
   2026-06-17=740.96, 2026-06-16=750.33), opens within ~1 bp (vendor auction-print differences);
   historical anchors match well-known reference closes (inception 1993-01-29=43.94,
   2018-12-24=234.34, 2020-03-23=222.95).
5. **Datetime normalization** — daily bars stored date-only ISO; confirmed.

## Known limitations carried into Step 3
- 2 missing sessions (2004-07-12, 2007-07-02) — drop spanning overnight returns.
- Dividend calendar empty 1997–2005 — ex-div overnight returns there are uncorrected.
- Latest dividend (2026-06-18) is provisional (series-boundary smear).
