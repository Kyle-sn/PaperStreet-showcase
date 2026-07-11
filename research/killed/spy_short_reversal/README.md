# SPY short-horizon reversal — PARKED at §7 (2026-06-14)

The Connors/Alvarez **RSI(2) < 10 + SMA(200) trend filter** candidate (`spy_short_reversal`),
long-only, single-entry, exit `close > SMA(5)`. It completed the full 9-step research workflow and
was **parked**: it passed in-sample (§4) and the §5 sensitivity plateau check, but **failed the
binding out-of-sample gate** — on 2015+ data its Sharpe (0.52) does **not** beat the 200-day
timing-only baseline (0.63) or buy-and-hold (0.70) on the committed risk-adjusted metric. The
reversion entry adds nothing OOS; the strong in-sample edge was the predicted crowding decay of a
heavily-published system. **One shot — not to be re-tuned or resurrected on OOS.**

**Authoritative write-up:** [`../research_notes/short_reversal_strategy_notes.md`](../research_notes/short_reversal_strategy_notes.md)
(§4 PASS, §5 PLATEAU, §7 FAIL → PARK, all decisions). This directory is the *code*; that note is the
*narrative*.

## Files

| File | Workflow step | What it does |
|---|---|---|
| `notebook.ipynb` | §3 | Signal characterization (descriptive; gate = GO). |
| `is_backtest.py` | §4 | In-sample three-way backtest (candidate vs `timing_sma` vs `buy_and_hold`) + §6 cost stress + acceptance checks. |
| `sensitivity.py` | §5 + Task 0 | Parameter sweep (plateau-vs-spike) and the idle-cash / risk-free Sharpe convention analysis (both conventions, side by side). |
| `oos_backtest.py` | §7 | The locked one-shot: frozen spec on 2015+, gates G1–G5, PASS/FAIL verdict. |

## Reproduce (offline, cache-first)

```bash
python -m research.killed.spy_short_reversal.is_backtest      # §4 in-sample
python -m research.killed.spy_short_reversal.sensitivity      # §5 sweep + cash-convention tables
python -m research.killed.spy_short_reversal.oos_backtest     # §7 one-shot (FAIL → PARK)
```

Raw data lives in `research/data/spy_daily_{adjusted,trades}.csv` (ADJUSTED_LAST = total-return is
the basis used throughout). The scripts seed the local `market_data_bars` cache from those CSVs, so
no TWS connection is needed.

## What outlived the candidate (reusable, not parked)

- The cost-equal **benchmark harness** (`strategy/benchmarks.py`: `buy_and_hold`, `timing_sma`).
- The **rf-aware Sharpe** (`backtesting.metrics.compute_metrics(risk_free_rate=...)`, default 0) and
  the idle-cash crediting analysis in `sensitivity.py`.
- The **`whatToShow`-configurable** fetch/loader/config path (total-return basis end-to-end).
- The bare **200-day timing overlay** (`timing_sma`) is the only signal here that survived OOS — the
  thing worth a future look, not the reversion entry.
