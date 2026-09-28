# Universe

The standing admissibility fence for PaperStreet instruments.

---

## What this document is, and is not

**Is:** a filter on what may be *considered* during strategy brainstorming. Its purpose is
efficiency: it prevents time spent framing a candidate that is out of scope on data or
structural grounds.

**Is not:**

- **Not a pre-committed universe.** Every candidate must still enumerate its own short
  instrument list in its `RESEARCH_WORKFLOW_<name>.md` before any data is pulled, with a
  written economic rationale per instrument. That per-candidate enumeration is what
  defends against survivorship and hindsight selection. This document does not substitute
  for it. See `STRATEGY.md` -> Instrument Universe Constraint.
- **Not a menu.** Candidate selection is hypothesis-first: state the economic rationale,
  then choose the instrument that expresses it. Browsing this list for "what could I do
  with X" inverts that order and is how data mining starts.
- **Not exhaustive.** If a hypothesis requires an instrument not listed here, the correct
  response is to add the instrument to this document with a rationale and a data-depth
  probe, not to abandon the hypothesis.

**Governing constraint:** PaperStreet does not purchase corporate-action, delisting, or
point-in-time universe data. Individual company shares are out of scope. See
`STRATEGY.md` -> Instrument Universe Constraint for the full rule and its re-entry
condition.

---

## Data depth by source (the binding constraint, not liquidity)

| Surface | Source | Depth | Implication |
|---|---|---|---|
| ETF daily | IBKR | Deep (SPY probed to 1993-01-29) | Best-supplied research surface. Full IS/OOS available. |
| ETF intraday (1-5 min) | IBKR | ~2014 (QQQ 5-min probed to 2014-01-02) | ~12y total. Supports roughly an 8y IS / 4y OOS split. Adequate but thin. |
| CME/CBOT/NYMEX/COMEX futures | Databento GLBX.MDP3 | 2010-06-06 hard floor | Accepted tradeoff (TSMOM Open Decision #3). No 2008 crisis datapoint. |
| CME futures | IBKR | 2.5-8y depending on instrument | Inadequate standalone. Probe result on file. |
| Cboe futures (VX, VXM) | **Not covered by GLBX.MDP3** | IBKR only, shallow | **Known gap.** See Tier 2. |

All inception dates and depths below are indicative. **Probe actual IBKR/Databento depth
per instrument before locking any candidate's universe**, consistent with existing
practice.

---

## Tier 1 — Core admissible

Deep history spanning a full IS+OOS window, high liquidity at both $50k and $5M, no known
structural discontinuities.

### ETFs — broad equity index

| Symbol | Exposure | Inception (approx) |
|---|---|---|
| SPY | S&P 500 | 1993-01 |
| QQQ | Nasdaq 100 | 1999-03 |
| IWM | Russell 2000 | 2000-05 |
| DIA | Dow 30 | 1998-01 |
| MDY | S&P Midcap 400 | 1995-05 |

### ETFs — US sector (Select Sector SPDR)

All launched 1998-12. Consistent methodology, deep history, high liquidity.

`XLE` energy, `XLF` financials, `XLK` technology, `XLV` healthcare, `XLI` industrials,
`XLP` staples, `XLY` discretionary, `XLU` utilities, `XLB` materials.

*Excluded from Tier 1:* `XLRE` (2015-10) and `XLC` (2018-06) have insufficient history and
were carved out of existing sectors, creating a definitional break in the sector set.

### ETFs — US fixed income

| Symbol | Exposure | Inception (approx) |
|---|---|---|
| TLT | 20+ year Treasury | 2002-07 |
| IEF | 7-10 year Treasury | 2002-07 |
| SHY | 1-3 year Treasury | 2002-07 |
| LQD | Investment grade credit | 2002-07 |
| AGG | Aggregate bond | 2003-09 |
| TIP | TIPS | 2003-12 |
| HYG | High yield credit | 2007-04 |
| JNK | High yield credit | 2007-11 |

### ETFs — commodity (physically backed)

| Symbol | Exposure | Inception (approx) | Note |
|---|---|---|---|
| GLD | Gold | 2004-11 | Physically backed. No roll. |
| SLV | Silver | 2006-04 | Physically backed. No roll. |

### ETFs — international equity

| Symbol | Exposure | Inception (approx) |
|---|---|---|
| EFA | Developed ex-US | 2001-08 |
| EEM | Emerging markets | 2003-04 |
| EWJ | Japan | 1996-03 |
| EWZ | Brazil | 2000-07 |
| FXI | China large cap | 2004-10 |

### Futures — equity index

Full-size and micro both admissible. **Micros are the default at $50k**; full-size is the
default at $5M.

| Full | Micro | Multiplier (full / micro) | Notes |
|---|---|---|---|
| ES | MES | $50 / $5 x index | Most liquid futures contract globally. |
| NQ | MNQ | $20 / $2 x index | |
| RTY | M2K | $50 / $5 x index | |
| YM | MYM | $5 / $0.50 x index | Thinner than ES/NQ. |

### Futures — rates

**Size on DV01, not notional.** ZN's ~$110k notional is misleading; its DV01 of roughly
$65/bp makes a single contract an ordinary position at $50k. Full-size is appropriate here
and the micro-preference rule does not apply.

| Symbol | Contract | Approx DV01 |
|---|---|---|
| ZT | 2-year Treasury note | ~$40/bp |
| ZF | 5-year Treasury note | ~$45/bp |
| ZN | 10-year Treasury note | ~$65/bp |
| ZB | 30-year Treasury bond | ~$180/bp |
| UB | Ultra Treasury bond | ~$250/bp |

DV01 figures move with the curve. Compute point-in-time, do not hardcode.

### Futures — energy and metals

| Full | Micro | Contract | Notes |
|---|---|---|---|
| CL | MCL | WTI crude (1000 / 100 bbl) | High roll yield. Ratio-adjust for signals. |
| GC | MGC | Gold (100 / 10 oz) | |
| SI | SIL | Silver (5000 / 1000 oz) | Micro is materially thinner. |
| HG | MHG | Copper (25000 / 2500 lb) | Micro is materially thinner. |

### Futures — FX

Full-size only in Tier 1. Micro FX is Tier 2 on spread cost.

`6E` euro, `6J` yen, `6B` sterling, `6A` Australian dollar, `6C` Canadian dollar,
`6S` Swiss franc.

---

## Tier 2 — Admissible with documented caveats

Usable, but the caveat must be addressed explicitly in the candidate's workflow doc before
Step 2 completes.

| Instrument(s) | Caveat |
|---|---|
| `VX`, `VXM` (Cboe VIX futures) | **Data-blocked.** Not in GLBX.MDP3 (CME Globex only). Requires IBKR's shallow futures history or a second Databento dataset. Structurally very attractive for a $50k account (VXM is $100 x VIX, roughly $1.5k notional) and `STRATEGY.md` already lists single-instrument vol/term-structure as an in-scope family. **Resolve the data source before framing a candidate.** |
| CME Micro Treasury Yield (`2YY`, `5YY`, `10Y`, `30Y`) | **Not smaller versions of ZT/ZF/ZN/ZB.** Yield-settled, fixed $10/bp DV01, inverted sign convention, no cheapest-to-deliver optionality. A price-based signal ported to these is wrong in a way that will not look wrong in a backtest. Use deliberately or not at all. |
| Micro FX (`M6E`, `M6A`, `M6B`) | Spread cost as a fraction of tick is materially worse than full-size. Acceptable for low-turnover candidates only; model costs explicitly. |
| `XLRE`, `XLC` | Short history (2015, 2018) and carved out of pre-existing sectors, so the sector set's definition changes mid-sample. Do not use in any study spanning the carve-out date without handling the break. |
| Industry ETFs: `XBI`, `XOP`, `XME`, `KRE`, `IYR`, `GDX`, `GDXJ` | History to 2006-2009. Adequate depth but shorter than Tier 1. Liquidity good. Acceptable where the hypothesis specifically requires industry granularity. |
| Currency ETFs: `UUP`, `FXE`, `FXY`, `FXB` | Thinner than the equivalent futures and carry expense-ratio drag. Prefer the futures contract unless there is a specific reason. |
| Broad commodity: `DBC`, `DBA`, `PDBC` | Roll methodology is a design choice made by the issuer and has changed historically. Read the current prospectus before assuming the exposure is what the name implies. |
| Leveraged/inverse: `TQQQ`, `SQQQ`, `UPRO`, `SPXU`, `TMF` | Path-dependent daily-reset compounding. Admissible **only** for candidates with explicitly short holding periods (intraday to a few days) where the decay is modeled. Multiple reverse splits in history. Never use in a multi-week hold. |
| `SMH` | Structure changed in 2011 (HOLDRS to VanEck ETF). Treat pre-2011 and post-2011 as different series. |

---

## Tier 3 — Excluded

Do not frame candidates around these. Each exclusion has a stated reason so it can be
challenged with evidence rather than re-litigated from scratch.

| Excluded | Reason |
|---|---|
| **All individual company shares** | Governing constraint. IBKR history is survivor-only; no delisting-inclusive source is funded. See `STRATEGY.md`. |
| **Any screened, ranked, or selected universe** | Reinstates survivorship bias regardless of asset class. Over a hundred US ETFs liquidate annually and IBKR serves no history for closed funds. Universes must be enumerated a priori. |
| `VXX`, `VXZ` and other volatility ETNs | `VXX` matured 2019-01 and was reissued as a new Series B note. The ticker has continuity but the instrument does not. This is a survivorship trap wearing an ETF costume. Also carries issuer credit risk. Use VX/VXM futures instead. |
| `XIV` | Terminated 2018-02-06. The canonical example of why backtests on inverse-vol ETPs lie. |
| `SVXY`, `UVXY` | Leverage was changed by the issuer in 2018-02 (SVXY -1x to -0.5x; UVXY 2x to 1.5x). The pre-2018 and post-2018 series are different instruments under one ticker. |
| `USO`, `UNG` | Roll methodology was materially restructured in 2020 following the negative-crude episode; `USO` also reverse-split 1:8. Pre-2020 and post-2020 are not the same exposure. Use CL/MCL futures instead. |
| ETFs launched after ~2013 | Insufficient history for a credible IS/OOS split at daily frequency. |
| Crypto futures (`BTC`, `MBT`, `ETH`, `MET`) | History begins 2017/2021. Insufficient sample, and no complete regime cycle. Revisit when depth allows. |
| Ags (`ZC`, `ZS`, `ZW`, `LE`, `HE`) | Not excluded on data grounds. Excluded pending a hypothesis that specifically needs agricultural exposure; seasonality and weather-driven regime structure require domain knowledge not currently on hand. Add with rationale if a candidate calls for it. |
| Single-stock options, ETF options | No options infrastructure in the system. Separate build, out of scope. |

---

## Adding to this document

An instrument is added by a candidate that needs it, not speculatively. The addition
requires:

1. A one-line economic rationale for why the hypothesis needs this instrument.
2. A data-depth probe result (source, earliest date, bar sizes available).
3. A structural-continuity check: any splits, methodology changes, issuer changes, or
   contract-spec changes within the intended window.
4. Placement in a tier, with the caveat written out if Tier 2.

---

## Change log

| Date | Change |
|---|---|
| 2026-07-30 | Initial version. Drafted alongside the `STRATEGY.md` Instrument Universe Constraint. |