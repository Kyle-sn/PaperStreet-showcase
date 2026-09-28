"""
alerts/

Off-box alerting for the live engine (see docs/DEPLOYMENT.md 7.3): a Discord
incoming webhook, called from a handful of choke points (kill-switch trip,
hard IBKR order rejection, stuck reconnect, unhandled trading-loop exception).
The Pi's dead-man's switch is a separate, non-Python consumer of the same
webhook URL, not part of this package.
"""

from alerts.discord import send_alert

__all__ = ["send_alert"]
