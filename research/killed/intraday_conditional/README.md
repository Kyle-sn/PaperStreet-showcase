# Intraday Conditional Strategy (QQQ) — STOPPED at Step 3 (2026-06-19)

The **first-hour → rest-of-day conditional** idea (H1): bucket each day by the first-hour return
normalized by trailing intraday ATR (`m = |r_1h|/ATR_pit`) and look for a sign that flips with
magnitude — small moves fade, large moves continue. **Step 3 EDA killed it in-sample.** The
conditional cross-tab shows no monotonic structure and no statistically significant bucket (all
|t| < 1.3), the only suggestive tail effect is gap momentum not a first-hour effect, and the tail
sign **inverts** at the 30- and 90-min windows. Per the workflow's anti-pattern rule the project
**stops**: no re-bucketing, no hypothesis pivot, and **OOS (2022–2025) is never opened**.

**Authoritative write-up:** [`../research_notes/intraday_conditional_strategy_notes.md`](../research_notes/intraday_conditional_strategy_notes.md)
(Step 3 verdict, committed feature/bucket definitions, all decisions). This directory is the *code*;
that note is the *narrative*.

## Files

| File | Workflow step | What it does |
|---|---|---|
| `fetch_5min_bars.py` | §2 | IBKR probe / monthly-walk fetch / external CSV loader into `market_data_bars`. |
| `quality_checks.py` | §2 | RTH-gap, half-day detection, DST, Yahoo spot-check; exports `half_days.json`. |
| `half_days.json` | §2 | Half-days (1pm ET close) + IBKR-gap days to drop from analysis. |
| `eda.py` | §3 | The reproducible workhorse: IS-only load, feature construction, the six EDA outputs, cost-floor overlay, saved plots, full readout. |
| `notebook.ipynb` | §3 | Narrative render of `eda.py` (pre-commit declarations + the six outputs inline) ending in the STOP verdict. |
| `plots/` | §3 | The six saved figures (`01_vol_profile` … `06_window_robustness`). |

## Reproduce (offline, cache-first)

```bash
python -m research.killed.intraday_conditional.eda            # §3 EDA: prints readout, writes plots/
jupyter nbconvert --to notebook --execute --inplace \
    research/killed/intraday_conditional/notebook.ipynb        # §3 narrative, plots embedded
```

Both read QQQ 5-min `TRADES` bars straight from the local `market_data_bars` cache — no TWS
connection needed. The data layer hard-bounds the query to `bar_datetime < '2022-01-01'`, so the
held-out OOS window is never loaded.

## Sample discipline (baked into the code)

- **IS = 2015-01-01 … 2021-12-31** is the only evaluated sample.
- **2014 = warm-up only** (feeds trailing ATR / overnight gap; never enters a statistic or plot).
- **OOS (2022+) never loaded** — enforced in SQL and re-asserted at runtime.
- Half-days and IBKR-gap days dropped (not imputed) via `half_days.json`.

## Why it stopped (the five gates, all failed)

| Gate | Result |
|---|---|
| Monotonic conditional structure (output 3) | Non-monotonic zigzag; all \|t\| < 1.3; SE ≈ the edge. |
| Survives cost floor in the tails (output 3) | No bucket distinguishable from zero → floor comparison moot. |
| Not overnight-gap momentum (output 4) | Only suggestive tail effect concentrates in large-gap days. |
| Stable across years (output 5) | Weak tail-sign persistence only; chaotic middle; sextile-6 flips in 2019. |
| Holds at 30/90-min windows (output 6) | **Decisive:** tail signs invert at both neighbouring windows. |

## What outlived the candidate (reusable)

- The Step-2 data infra (`fetch_5min_bars.py`, `quality_checks.py`, `half_days.json`) — a clean
  QQQ 5-min `TRADES` history in `market_data_bars`.
- `eda.py`'s **point-in-time intraday-feature scaffolding** (DST-correct ET conversion, START-labeled
  bar-edge handling, point-in-time trailing-ATR normalization, fixed-edge sextile cross-tabs with a
  cost-floor overlay) — reusable for any future intraday-bar signal study.
