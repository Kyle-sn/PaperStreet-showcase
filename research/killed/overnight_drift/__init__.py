"""Overnight-drift research package (Step 2 — data + dividend calendar).

See research/research_notes/RESEARCH_WORKFLOW_overnight_drift.md for the workflow.
This package is research-tier: nothing here touches strategy/ or orders/, and the
dividend calendar lives as a CSV in this package, not as a DB table (no schema is
added until the candidate survives the kill gates).
"""
