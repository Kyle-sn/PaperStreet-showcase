"""
basket_statarb — research-tier machinery for cointegrated-basket stat-arb.

Stage 0 (this package): the reusable spread/cointegration primitives plus a
research-tier daily spread simulation, proven on a known-good basket
(GOOG/GOOGL). See research/killed/research_notes/RESEARCH_WORKFLOW_basket_statarb.md
for the staged plan and design decisions, and
research/killed/research_notes/basket_statarb_proposal.md for full context.

Research-tier means: pandas/numpy/statsmodels only. No NES, no numba, no
Databento, no optimizer. The daily sim here is research tooling for inspecting
candidate spreads — it is explicitly NOT the validation gate. The PaperStreet
backtesting engine remains the real gate at Stage 1 (see docs/ROADMAP.md ->
Decided Against, and docs/BACKTESTING.md).

Modules
-------
cointegration  Box-Tiao (primary) + Johansen (cross-check) -> weight vectors;
               raw basis (Open Decision #1 — LOCKED), price_basis kept general
spread         raw-price spread, rolling z-score, ADF, OU half-life, weight stability
costs          IBKR commission, half-spread slippage, borrow, dividends-on-shorts
sim            per-basket daily long/short-spread state machine with gap-aware stops
data           cache-first daily ADJUSTED_LAST loader + TWS prewarm
known_good     GOOG/GOOGL-vs-unrelated-pairs validation harness
"""
