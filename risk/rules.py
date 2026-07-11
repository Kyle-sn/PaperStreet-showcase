"""
rules.py

Individual pre-trade risk rules and the decision object they return.

Each rule is a self-contained object with a single `check(order, contract,
account_state) -> RiskDecision` method, so it can be unit-tested in isolation
against a fabricated `AccountState` (no TWS, no order handler). `RiskGate`
(see `gate.py`) composes them; it does not inline any of this logic.

Two rules are **latching / sticky** by design — once tripped they stay tripped
for the session and require a manual `reset()`:
- `KillSwitchRule`: a manual halt flag.
- `DailyLossLimitRule`: trips when session PnL breaches the loss limit and must
  NOT un-trip when a later (≤3-min-stale) heartbeat shows PnL recovering. The
  latch is what makes the circuit breaker a breaker and not a flapping gate.
"""

from __future__ import annotations

import time

from utils.log_config import setup_logger

logger = setup_logger(__name__)

# IBKR unset-double sentinel; an unset limit price on a market order shows up as
# this (or 0.0), neither of which is a usable price for a notional check.
_UNSET_DOUBLE = 1.7976931348623157e308


class RiskDecision:
    """Result of a risk check: an approval, or a rejection carrying the rule
    name and a human-readable reason."""

    __slots__ = ("approved", "rule", "reason")

    def __init__(self, approved: bool, rule: str | None = None, reason: str | None = None):
        self.approved = approved
        self.rule = rule
        self.reason = reason

    @classmethod
    def approve(cls) -> "RiskDecision":
        return cls(True)

    @classmethod
    def reject(cls, rule: str, reason: str) -> "RiskDecision":
        return cls(False, rule=rule, reason=reason)

    def __repr__(self) -> str:
        if self.approved:
            return "RiskDecision(approve)"
        return f"RiskDecision(reject, rule={self.rule!r}, reason={self.reason!r})"


class RiskRule:
    """Base class. A rule inspects the order/contract/account state and returns
    a `RiskDecision`. `name` is used in logs and rejection reasons."""

    name = "rule"

    def check(self, order, contract, account_state) -> RiskDecision:  # pragma: no cover
        raise NotImplementedError


def _limit_or_reference_price(order, contract, account_state) -> float | None:
    """Best available price for a notional estimate: the order's own limit price
    when it has a usable one, else the last market price of the held position.
    None when neither exists (e.g. a market order opening a fresh position)."""
    lmt = getattr(order, "lmtPrice", None)
    if lmt is not None and 0.0 < lmt < 1e307:
        return lmt
    if account_state is not None:
        return account_state.reference_price(contract.symbol)
    return None


class OrderSizeRule(RiskRule):
    """Per-order size cap — a share cap and/or a notional cap.

    The share cap always applies. The notional cap applies only when a price is
    resolvable (limit price, or the position's last market price); for a market
    order opening a fresh position no mark exists, so the notional cap cannot be
    evaluated and is skipped with a warning rather than fail-closed (fail-closed
    would block every market-order entry). The share cap is the always-on guard;
    see `docs/RISK.md`.
    """

    name = "order_size"

    def __init__(self, max_shares: float | None = None, max_notional: float | None = None):
        self.max_shares = max_shares
        self.max_notional = max_notional

    def check(self, order, contract, account_state) -> RiskDecision:
        qty = float(order.totalQuantity)

        if self.max_shares is not None and qty > self.max_shares:
            return RiskDecision.reject(
                self.name,
                f"order quantity {qty:g} exceeds per-order share cap {self.max_shares:g}",
            )

        if self.max_notional is not None:
            price = _limit_or_reference_price(order, contract, account_state)
            if price is None:
                logger.warning(
                    f"OrderSizeRule: no price available for {contract.symbol}; "
                    f"per-order notional cap not enforced for this order"
                )
            else:
                notional = qty * price
                if notional > self.max_notional:
                    return RiskDecision.reject(
                        self.name,
                        f"order notional ${notional:,.0f} (qty {qty:g} @ ${price:,.2f}) "
                        f"exceeds per-order notional cap ${self.max_notional:,.0f}",
                    )

        return RiskDecision.approve()


class KillSwitchRule(RiskRule):
    """Manual, sticky halt. When active, rejects every order until `reset()`.

    Same sticky-flag pattern as `DailyLossLimitRule`: flipping it on is a
    deliberate act (a person or a supervisory process); it never clears itself.
    """

    name = "kill_switch"

    def __init__(self, tripped: bool = False):
        self._tripped = tripped
        self._reason = "kill switch armed at startup" if tripped else None

    @property
    def tripped(self) -> bool:
        return self._tripped

    def trip(self, reason: str = "manual") -> None:
        self._tripped = True
        self._reason = reason
        logger.warning(f"Kill switch TRIPPED: {reason}")

    def reset(self) -> None:
        self._tripped = False
        self._reason = None
        logger.warning("Kill switch RESET")

    def check(self, order, contract, account_state) -> RiskDecision:
        if self._tripped:
            return RiskDecision.reject(
                self.name, f"kill switch active — all orders halted ({self._reason})"
            )
        return RiskDecision.approve()


class ConnectionLivenessRule(RiskRule):
    """Stale-data / connection-liveness guard.

    Rejects when the connection is down, no account heartbeat has been received
    yet, or the last heartbeat is older than `max_staleness_seconds`. This is a
    pre-trade check only — it does NOT reconnect or re-subscribe (reconnect
    logic is a separate, deferred build; see `docs/RISK.md` / `ROADMAP.md`).

    The threshold must exceed IBKR's ≤3-min (180s) account-update cadence, or a
    live-but-quiet connection trips falsely; the default is set well above it.
    `now_fn` is injectable so the age comparison is deterministic in tests.
    """

    name = "connection_liveness"

    def __init__(self, max_staleness_seconds: float, now_fn=time.time):
        self.max_staleness_seconds = max_staleness_seconds
        self._now = now_fn

    def check(self, order, contract, account_state) -> RiskDecision:
        if account_state is None or not account_state.is_connected:
            return RiskDecision.reject(self.name, "connection not confirmed live")

        heartbeat = account_state.last_heartbeat
        if heartbeat is None:
            return RiskDecision.reject(self.name, "no account heartbeat received yet")

        age = self._now() - heartbeat
        if age > self.max_staleness_seconds:
            return RiskDecision.reject(
                self.name,
                f"account data stale: last heartbeat {age:.0f}s ago "
                f"exceeds {self.max_staleness_seconds:.0f}s threshold",
            )

        return RiskDecision.approve()


class DailyLossLimitRule(RiskRule):
    """Latching session loss limit (circuit breaker).

    Trips when session PnL (realized + unrealized) drops to or below
    `loss_limit` (a negative number). Once tripped it stays tripped for the
    session regardless of any subsequent PnL recovery, and requires a manual
    `reset()` — this latch is the whole point: a breaker that un-trips on the
    next heartbeat showing a bounce is not a breaker.

    PnL is read from the account snapshot, so enforcement is only as fresh as
    the ≤3-min account-update cadence — a deliberate, documented granularity
    (see `docs/RISK.md`). When PnL is unavailable (no data yet) the rule cannot
    judge and approves; the liveness rule is what guards missing data.
    """

    name = "daily_loss_limit"

    def __init__(self, loss_limit: float):
        self.loss_limit = loss_limit
        self._tripped = False

    @property
    def tripped(self) -> bool:
        return self._tripped

    def reset(self) -> None:
        self._tripped = False
        logger.warning("Daily loss limit RESET")

    def check(self, order, contract, account_state) -> RiskDecision:
        # Sticky: stay tripped for the session once breached.
        if self._tripped:
            return RiskDecision.reject(
                self.name,
                f"daily loss limit tripped (limit ${self.loss_limit:,.0f}) — "
                f"halted for the session, manual reset required",
            )

        pnl = account_state.session_pnl if account_state is not None else None
        if pnl is not None and pnl <= self.loss_limit:
            self._tripped = True
            logger.warning(
                f"Daily loss limit TRIPPED: session PnL ${pnl:,.0f} "
                f"breached limit ${self.loss_limit:,.0f}"
            )
            return RiskDecision.reject(
                self.name,
                f"session PnL ${pnl:,.0f} breached daily loss limit "
                f"${self.loss_limit:,.0f} — latching, manual reset required",
            )

        return RiskDecision.approve()
