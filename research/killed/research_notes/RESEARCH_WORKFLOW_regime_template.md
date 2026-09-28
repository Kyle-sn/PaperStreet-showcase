# Regime Branch — Workflow Addendum Template (regime candidates only)

Copy-in variant blocks for a `RESEARCH_WORKFLOW_<name>.md` whose thesis is **explicitly
regime-conditional**. These blocks are an **opt-in branch** on the standard workflow, not a
replacement for it. A non-regime candidate's workflow doc does **not** include them, and the
single-split in-sample backtest plus one-shot OOS remains the default path for every other
candidate. See `docs/STRATEGY.md` → "Regime-Switching and Adaptive Strategies" and
`docs/BACKTESTING.md` → "Validation methods" for the rationale.

**Canonical step numbering (1-indexed).** PaperStreet workflow docs are 1-indexed:
1 Framing · 2 Universe & Data · 3 Signal · 4 In-Sample Backtest · 5 Parameter Sensitivity ·
6 Cost Stress · 7 OOS one-shot · 8 Paper · 9 Pre-Live (→ Live). Insert each block below under its
correspondingly-named step.

The killed `research/killed/research_notes/RESEARCH_WORKFLOW_diversified_trend.md` is 0-indexed and is a
frozen historical exception — it self-documents its own scheme and is not the template to copy.

---

## Under **Universe and Data** (Step 2)

> **[REGIME BRANCH]** Before enumerating the candidate universe, confirm every instrument —
> including the regime detector's exogenous variable, not just the in-regime signal's
> instruments — clears `docs/STRATEGY.md` → "Instrument Universe Constraint": ETFs/ETNs or
> listed futures only, explicitly enumerated in this doc with rationale and a verified listing
> history over the full IS+OOS window. A detector keyed to a screened or single-name series
> carries the same survivorship bias as the signal it gates.
>
> Additionally, confirm every instrument (detector and signal alike) is drawn from
> `docs/UNIVERSE.md` Tier 1 or Tier 2. Tier 1 needs no further comment. Any Tier 2 instrument
> requires its listed caveat addressed in writing, here, before Step 2 completes — not deferred
> to the backtest. An instrument absent from `UNIVERSE.md` entirely, or listed in Tier 3, is not
> admissible without first amending `UNIVERSE.md` per its "Adding to this document" section.

---

## Under **Signal** (Step 3)

> **[REGIME BRANCH]** This candidate is regime-conditional, so the signal step has two
> objectives, kept separate:
>
> - **Regime detector.** Variable: `<exogenous variable — e.g. close vs SMA(long), trailing
>   realized vol percentile, term-structure sign>`. Threshold: `<value>`, set from
>   `<structural/economic argument>` and **committed here before any conditional backtest**.
>   States: `<≤3, named>`. The detector is NOT grid-searched against conditional Sharpe.
> - **In-regime signal.** `<≤2–3 parameters>`. Same simplicity bar as any default candidate.
>
> Pre-commit the detector definition and threshold in this block before looking at any
> conditional performance. Tuning the detector to maximize conditional Sharpe reintroduces
> everything the split is meant to prevent.

---

## Under **In-Sample Backtest** (Step 4)

> **[REGIME BRANCH]** Validation here is **walk-forward / purged CPCV inside the IS window**,
> not a single IS backtest. Required reporting:
>
> - Per-*episode* performance (each distinct occurrence of each state), not a pooled
>   per-regime Sharpe — and the count of episodes behind each estimate.
> - Which folds actually contained each state (a fold with zero instances of a state says
>   nothing about it).
> - The **unconditional** strategy as the binding benchmark: same base signal run always-on,
>   through the same engine/costs. The gated version must beat it net of the gating turnover.
>   Beating buy-and-hold / cash while failing to beat unconditional ⇒ the gate did nothing.
> - Purge/embargo applied if and only if labels overlap (state this explicitly either way).

---

## Under **OOS one-shot** (Step 7)

> **[REGIME BRANCH]** Before spending the OOS, report the **deflated Sharpe** of the
> IS-selected candidate. Trial count N must include the detector search
> (detector configs × signal configs), not just the in-regime signal grid. The OOS remains a
> single held-out shot; the deflated-Sharpe step corrects the IS selection that produced the
> locked candidate, it does not change the one-shot rule.
