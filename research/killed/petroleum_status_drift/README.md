# Petroleum Status Drift (Crude / MCL) — Step 2 Data Readout

Research-tier package for the petroleum-status-drift candidate (EIA Weekly Petroleum Status
Report crude-inventory surprise → multi-day drift in front-month WTI). Workflow + hypothesis +
kill criteria: `research/killed/research_notes/RESEARCH_WORKFLOW_petroleum_status_drift.md`.

**Status: PARKED at Step 2 (G0 data-depth gate FAILS).** No signal, EDA, or backtest code exists
in this package — Steps 3–9 were never opened. See the workflow doc for full detail; summary
below.

**Scope of this package (Step 2 only):** IBKR price-depth probe, EIA event data pull, and MCL
contract-spec sourcing. No event study, no surprise model, no strategy/engine code.

## Contents
- `ibkr_depth_probe.py` — `probe` / `fetch` / `report`. Walks IBKR CL (CONTFUT + individual dated
  FUT) historical depth and flags degenerate placeholder bars (`_flat_bar_mask`:
  `open==high==low==close and volume==0`).
- `eia_fetch.py` — `fetch` / `validate`. Pulls full EIA WCESTUS1 (weekly crude stocks) history via
  api.eia.gov v2 and derives a point-in-time release-date calendar.
- `mcl_contract_specs.md` — MCL contract specs for the eventual G3 cost gate, sourced and
  confidence-tagged (CONFIRMED / CORROBORATED / UNRESOLVED).
- `data/eia_weekly_crude_stocks.csv` — 2,290 weekly releases, 1982-08-20 → 2026-08-14.
- `data/raw/` — archived raw EIA API responses.

## Why parked: the G0 gate

A CONTFUT max-duration pull nominally spans 2018-01-24 → present (2,116 daily bars), which looked
like a G0 pass (448 events) — but 73% of those bars (everything before 2024-09-17) are degenerate
placeholders (`open==high==low==close`, `volume==0`), most likely from a missing NYMEX energy
historical-data entitlement on this account. Confirmed via the 2020-04-20 negative-settle day,
which reads a flat positive $38.83 in the cached bars — not real. Real, dense, non-degenerate CL
data on this account only starts **2024-09-17** (486/486 clean bars through 2026-08-24), giving
**101 usable EIA events** over the actual usable window — below the pre-committed G0 floor of 200.
Per the frozen kill-criteria table in the workflow doc: **do not proceed to Step 3/backtest.**

Individual dated FUT contracts don't offer a workaround either — on this account they only
resolve via symbol+month lookup for roughly the trailing 12–13 months (tested back through 2001),
a *shorter* window than CONTFUT's real depth. Reaching further back would need
`reqContractDetails`-based conId resolution, not attempted (out of scope for a raw-acquisition
pass). See `docs/IBKR_NOTES.md` for the general IBKR-gotcha writeup (this could silently corrupt
any futures pull that only checks date range, not bar contents).

## What's resolved vs. still open

- **IBKR CL depth (Open Decision #2):** RESOLVED — gate fails as above.
- **EIA event data + release calendar (Open Decision #1, data half):** RESOLVED — full history
  cached, calendar validated 14/14 against EIA's holiday-shift exception table. The
  *expectation-model* half of OD#1 (point-in-time surprise definition) is still open — Step 3 work,
  never started.
- **MCL contract specs:** size/tick/settlement CONFIRMED; commission CORROBORATED (not
  primary-fetched); **bid-ask spread UNRESOLVED** — needs a live IBKR quote sample, which needs
  `tickPrice`/`contractDetails` EWrapper support `IBApp` doesn't currently have (a shared-infra
  change, out of scope for this session).

## Concrete next step if revived

A data subscription upgrade (or a different account/vendor with real NYMEX energy history) is the
actual blocker — 101 events over 23 months needs deeper history, not a bigger cache of the same
window. Once real CL depth clears 200 events, resume at Step 3 (Open Decisions #1, #3–#7 in the
workflow doc are still open and must be resolved first).
