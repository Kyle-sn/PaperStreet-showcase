# MCL (Micro WTI Crude Oil futures) — contract specs for the G3 cost gate

Pulled 2026-08-24. Every field below is tagged with a confidence level —
**CONFIRMED** (primary/authoritative source reached directly),
**CORROBORATED** (2+ independent secondary sources agree, primary source
blocked), or **UNRESOLVED** (not obtainable from a spec page at all; needs
a live measurement). Nothing here is fabricated — an UNRESOLVED field is
left blank rather than guessed.

CME's own site (`cmegroup.com`) and IBKR's own site
(`interactivebrokers.com`) both blocked automated fetches (403 / timeout)
during this session, so the CONFIRMED tier below relies on two independent
retail-broker spec pages that mirror CME's published numbers verbatim
(Ironbeam, MetroTrade), cross-checked against each other and against
CME's own education-site copy surfaced in search snippets. These are not
CME/IBKR primary pages, but they are independent of each other and agree
exactly on every numeric field — recommend a final confirm via
`reqContractDetails` once TWS is connected (Step 2 follow-up).

## Contract specs — CONFIRMED (cross-corroborated, not fabricated)

| Field | Value | Source |
|---|---|---|
| Underlying | CME/NYMEX WTI Light Sweet Crude Oil, 1/10th the size of CL | Ironbeam, MetroTrade |
| Contract size | 100 barrels | Ironbeam, MetroTrade |
| Minimum tick size | $0.01 / barrel | Ironbeam, MetroTrade |
| Minimum tick value | $1.00 / contract (100 bbl x $0.01) | Ironbeam, MetroTrade |
| Exchange | CME Globex | MetroTrade |
| Product code | MCL | Ironbeam, MetroTrade |
| Settlement | Financially (cash) settled — settles to CL's final settlement price on the same expiration schedule, no physical delivery | MetroTrade |
| Trading hours | Sun 5:00pm - Fri 4:00pm CT, with a daily 4:00-5:00pm CT halt | Ironbeam, MetroTrade |
| Listing | Monthly contracts; expire one business day before the corresponding CL contract month | search corroboration (CME education page) |

## Commission — CORROBORATED, needs live confirmation

| Field | Value | Source | Confidence |
|---|---|---|---|
| IBKR commission (fixed) | USD 0.25 / contract / side | search-indexed copy of `interactivebrokers.com/en/pricing/commissions-futures.php` (direct fetch 403'd) | CORROBORATED, not primary-fetched |
| IBKR commission (tiered) | USD 0.10-0.25 / contract / side, volume-dependent | same | CORROBORATED, not primary-fetched |
| Exchange + regulatory fees | ~$0.70 / contract / side (one third-party estimate; not IBKR's own published number) | brokerchooser.com | LOW CONFIDENCE — third-party estimate only |

**Do not treat the ~$0.95/contract/side all-in total as final.** IBKR's
actual commission depends on account tier and can change; the exchange-fee
component specifically was not confirmed from IBKR's own fee page (also
403'd — `en/accounts/fees/NYMEX.php`). **Action item for Step 2 follow-up
(needs TWS connected):** pull the live commission via a what-if order
(`placeOrder` with `whatIf=True`) or read it off an actual paper-account
fill — that is IBKR's own authoritative number for this account, and
removes the need to trust a scraped commission page at all.

## Typical bid-ask spread — UNRESOLVED

Not obtainable from any spec page — spread is a live microstructure
property, not a published contract term, and varies by session (US
day-session vs. Asia/London overnight) and by regime. **Needs one of:**
- A live IBKR quote sample (`reqMktData` snapshot or a short streaming
  capture) across a few sessions, once TWS is connected, or
- An estimate from a trade-tape/quote-history vendor.

Left blank rather than guessed. This blocks G3 (cost gate) until filled in
— flag for Desktop.

## Provenance

- CME/contract-spec direct fetch attempts (`cmegroup.com/.../micro-wti-crude-oil.contractSpecs.html`,
  `.../micro-wti-crude-oil.html`, fact-card PDF) all timed out in this session.
- IBKR direct fetch attempts (`commissions-futures.php`, `cme-micro-crude.php`,
  `accounts/fees/NYMEX.php`, both `.com` and `investors.` mirrors) all returned
  403.
- Secondary sources used instead: Ironbeam KB, MetroTrade KB, brokerchooser.com,
  plus search-engine-indexed snippets of the IBKR/CME pages themselves.
