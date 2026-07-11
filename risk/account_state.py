"""
account_state.py

The snapshot of broker/account state that `RiskGate` reads when vetting an order.

Why a snapshot and not the live `IBApp`
---------------------------------------
The order handler runs as its own `IBApp` on `ORDERS_CLIENT_ID`, which never
subscribes to account/portfolio updates (see `MULTI_STRATEGY_REFACTOR.md`
Phase 2) — so it has no PnL, no positions, no heartbeat. That data lives on the
*live-engine* `Session` (`LIVE_ENGINE_CLIENT_ID`). Passing a plain `AccountState`
into `RiskGate.check` decouples the gate from *which* connection holds the data
and makes every rule trivially testable against a fabricated state — no TWS, no
mocked app.

All PnL figures are IBKR-reported and therefore only as fresh as the account
update cadence (≤3 min; see `docs/RISK.md` on the daily-loss-limit granularity).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AccountState:
    """
    Point-in-time account state consumed by the risk rules.

    Attributes
    ----------
    realized_pnl, unrealized_pnl : float | None
        IBKR-reported session PnL components (from `self.account`). None until
        the first account update arrives.
    last_heartbeat : float | None
        Epoch seconds (`time.time()`) of the most recent account/portfolio
        callback. None before any update — treated as "not confirmed live".
    is_connected : bool
        Whether the underlying connection reports itself connected.
    positions : dict
        Copy of the broker positions dict (see `docs/DATA_MODEL.md`
        `self.positions`), used to resolve a reference price for the notional
        cap on market orders (which carry no limit price).
    """

    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    last_heartbeat: float | None = None
    is_connected: bool = True
    positions: dict = field(default_factory=dict)

    @property
    def session_pnl(self) -> float | None:
        """Realized + unrealized PnL, or None if neither has been received yet.

        A missing component is treated as 0 so a partial update still yields a
        usable figure; None only when both are absent (nothing to judge on)."""
        if self.realized_pnl is None and self.unrealized_pnl is None:
            return None
        return (self.realized_pnl or 0.0) + (self.unrealized_pnl or 0.0)

    def reference_price(self, symbol: str) -> float | None:
        """Last known market price for `symbol` from the positions snapshot, or
        None if no position is held (so no mark is available)."""
        entry = self.positions.get(symbol) if self.positions else None
        if not entry:
            return None
        return entry.get("market_price")

    @classmethod
    def from_session(cls, session) -> "AccountState":
        """Build a snapshot from a live `research.session.Session`.

        Returns a fully-defaulted state (heartbeat None → liveness rule rejects)
        when the session has no account subscription."""
        return cls(
            realized_pnl=session.get_realized_pnl(),
            unrealized_pnl=session.get_unrealized_pnl(),
            last_heartbeat=session.get_last_heartbeat(),
            is_connected=session.is_connected,
            positions=session.get_all_positions() or {},
        )
