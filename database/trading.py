import uuid
from typing import Optional
from .db import get_connection


def save_order(
    symbol: str,
    action: str,
    order_type: str,
    quantity: float,
    sec_type: str = "STK",
    tif: str = "DAY",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    trail_percent: Optional[float] = None,
    trail_amount: Optional[float] = None,
    outside_rth: bool = False,
    ib_order_id: Optional[int] = None,
    ib_parent_id: Optional[int] = None,
    strategy_name: Optional[str] = None,
    trade_group_id: Optional[str] = None,
) -> int:
    """Insert a new order row and return its local primary key."""
    sql = """
        INSERT INTO orders
            (symbol, sec_type, action, order_type, tif, quantity,
             limit_price, stop_price, trail_percent, trail_amount,
             outside_rth, ib_order_id, ib_parent_id, remaining_quantity, strategy_name,
             trade_group_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with get_connection() as conn:
        cur = conn.execute(sql, (
            symbol, sec_type, action, order_type, tif, quantity,
            limit_price, stop_price, trail_percent, trail_amount,
            1 if outside_rth else 0,
            ib_order_id, ib_parent_id, quantity, strategy_name,
            trade_group_id,
        ))
        return cur.lastrowid


def assign_trade_group(
    strategy_name: Optional[str],
    symbol: str,
    action: str,
    quantity: Optional[float] = None,
) -> str:
    """Return the trade_group_id an order for (strategy, symbol) belongs to, minting one if needed.

    The `trades` table is the open-group registry. If there is no open trade for
    (strategy_name, symbol), this order opens a new logical trade: a fresh UUID is
    minted and an `open` trades row is inserted (recording the entry side). If an
    open trade already exists, the order joins it (an exit, or a same-direction
    scale-in) and its existing id is returned — the open->closed transition is
    *not* made here; it is driven by fill reconciliation in `save_execution` once
    the group's net filled shares return to 0.

    `strategy_name` is matched with `IS` so None-keyed (strategy-less) orders group
    correctly too.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT trade_group_id FROM trades "
            "WHERE strategy_name IS ? AND symbols = ? AND status = 'open' "
            "ORDER BY id DESC LIMIT 1",
            (strategy_name, symbol),
        ).fetchone()
        if row is not None:
            return row["trade_group_id"]

        group_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO trades (trade_group_id, strategy_name, symbols, side, quantity) "
            "VALUES (?, ?, ?, ?, ?)",
            (group_id, strategy_name, symbol, action, quantity),
        )
        return group_id


def update_order_status(
    order_id: int,
    status: str,
    filled_quantity: float = 0,
    remaining_quantity: Optional[float] = None,
    avg_fill_price: Optional[float] = None,
    last_fill_price: Optional[float] = None,
    ib_perm_id: Optional[int] = None,
    why_held: Optional[str] = None,
) -> None:
    """Update an order's status from an orderStatus() callback."""
    sql = """
        UPDATE orders SET
            status             = ?,
            filled_quantity    = ?,
            remaining_quantity = ?,
            avg_fill_price     = ?,
            last_fill_price    = ?,
            ib_perm_id         = COALESCE(?, ib_perm_id),
            why_held           = ?,
            updated_at         = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
    """
    with get_connection() as conn:
        conn.execute(sql, (
            status, filled_quantity, remaining_quantity,
            avg_fill_price, last_fill_price, ib_perm_id, why_held, order_id,
        ))


def link_ib_order_id(order_id: int, ib_order_id: int) -> None:
    """Associate an IB-assigned order ID with a locally-created order row."""
    with get_connection() as conn:
        conn.execute("UPDATE orders SET ib_order_id = ? WHERE id = ?", (ib_order_id, order_id))


def save_execution(
    symbol: str,
    side: str,
    shares: float,
    price: float,
    executed_at: str,
    ib_exec_id: Optional[str] = None,
    ib_order_id: Optional[int] = None,
    order_id: Optional[int] = None,
    account: Optional[str] = None,
    sec_type: str = "STK",
    cum_qty: Optional[float] = None,
    avg_price: Optional[float] = None,
    commission: Optional[float] = None,
    commission_currency: Optional[str] = None,
    realized_pnl: Optional[float] = None,
    liquidation: bool = False,
    exchange: Optional[str] = None,
) -> int:
    """Record a fill from an execDetails() + commissionAndFeesReport() callback. Idempotent on ib_exec_id."""
    sql = """
        INSERT OR IGNORE INTO executions
            (order_id, ib_exec_id, ib_order_id, account, symbol, sec_type,
             side, shares, price, cum_qty, avg_price,
             commission, commission_currency, realized_pnl, liquidation, exchange, executed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with get_connection() as conn:
        cur = conn.execute(sql, (
            order_id, ib_exec_id, ib_order_id, account, symbol, sec_type,
            side, shares, price, cum_qty, avg_price,
            commission, commission_currency, realized_pnl,
            1 if liquidation else 0, exchange, executed_at,
        ))
        # Only reconcile on a genuinely new fill (INSERT OR IGNORE makes this
        # idempotent: a duplicate ib_exec_id changes nothing and rowcount is 0).
        if cur.rowcount:
            _reconcile_trade_on_fill(conn, order_id, ib_order_id)
        return cur.lastrowid


def _reconcile_trade_on_fill(conn, order_id, ib_order_id) -> None:
    """Close the fill's trade group once its net filled shares return to flat.

    A logical trade is `open` from entry submission. Each fill moves the group's
    net signed filled shares (BOT positive, SLD negative); when they sum back to 0
    the round trip is complete and the trades row transitions open->closed. Partial
    exits leave a non-zero net and keep it open. Runs inside the execution insert's
    transaction so the fill and the close are atomic.
    """
    group_id = None
    if order_id is not None:
        r = conn.execute("SELECT trade_group_id FROM orders WHERE id = ?", (order_id,)).fetchone()
        group_id = r["trade_group_id"] if r else None
    if group_id is None and ib_order_id is not None:
        r = conn.execute(
            "SELECT trade_group_id FROM orders WHERE ib_order_id = ?", (ib_order_id,)
        ).fetchone()
        group_id = r["trade_group_id"] if r else None
    if group_id is None:
        return

    net = conn.execute(
        "SELECT COALESCE(SUM(CASE e.side WHEN 'BOT' THEN e.shares ELSE -e.shares END), 0) "
        "FROM executions e JOIN orders o ON o.id = e.order_id "
        "WHERE o.trade_group_id = ?",
        (group_id,),
    ).fetchone()[0]
    if net == 0:
        conn.execute(
            "UPDATE trades SET status = 'closed', "
            "closed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE trade_group_id = ? AND status = 'open'",
            (group_id,),
        )


def migrate_orders_trade_group() -> bool:
    """Add orders.trade_group_id to a pre-Phase-2 database. Idempotent; returns True if it ran.

    CREATE TABLE IF NOT EXISTS in schema.sql does not alter an existing `orders`
    table, so an established database needs the column added explicitly. Fresh
    databases already have it from the schema and this is a no-op.
    """
    with get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "trade_group_id" in cols:
            return False
        conn.execute("ALTER TABLE orders ADD COLUMN trade_group_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_trade_group ON orders(trade_group_id)"
        )
        return True


def save_signal(
    strategy_name: str,
    symbol: str,
    action: Optional[str],
    quantity: Optional[int],
    bar_datetime: Optional[str] = None,
    bar_close: Optional[float] = None,
) -> int:
    """Persist a signal emitted by a strategy (None action = no-op bar)."""
    sql = """
        INSERT INTO strategy_signals
            (strategy_name, symbol, action, quantity, bar_datetime, bar_close)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    with get_connection() as conn:
        cur = conn.execute(sql, (strategy_name, symbol, action, quantity, bar_datetime, bar_close))
        return cur.lastrowid


def mark_signal_executed(signal_id: int, order_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE strategy_signals SET executed = 1, order_id = ? WHERE id = ?",
            (order_id, signal_id),
        )


def get_order_db_id(ib_order_id: Optional[int] = None, ib_perm_id: Optional[int] = None) -> Optional[int]:
    """Resolve a local orders.id from IBKR identifiers.

    ib_perm_id is globally unique and checked first when present. ib_order_id
    is only unique within a single API session -- IBKR resets its order-id
    counter to 1 on every Gateway restart, so distinct sessions' orders can
    share the same value. The ib_order_id fallback matches the most recently
    created row with that id: a row is always inserted immediately before
    placeOrder() is called, so the newest one is the order currently in
    flight, not a stale row from an earlier session (see DATA_MODEL.md ->
    orders, the ib_perm_id correlation-gap note).
    """
    with get_connection() as conn:
        if ib_perm_id:
            row = conn.execute(
                "SELECT id FROM orders WHERE ib_perm_id = ?", (ib_perm_id,)
            ).fetchone()
            if row:
                return row[0]
        if ib_order_id is None:
            return None
        row = conn.execute(
            "SELECT id FROM orders WHERE ib_order_id = ? ORDER BY id DESC LIMIT 1",
            (ib_order_id,),
        ).fetchone()
    return row[0] if row else None


def update_order_status_by_ib_id(
    ib_order_id: int,
    status: str,
    filled_quantity: float = 0,
    remaining_quantity: Optional[float] = None,
    avg_fill_price: Optional[float] = None,
    last_fill_price: Optional[float] = None,
    ib_perm_id: Optional[int] = None,
    why_held: Optional[str] = None,
) -> None:
    """Update order status from an orderStatus()/error() callback.

    Resolves the target row via get_order_db_id() (ib_perm_id-preferred, see
    its docstring) and updates exactly that one row by primary key -- fixes a
    real production incident where writing `WHERE ib_order_id = ?` directly
    landed a single fill's status on 6 unrelated historical rows that
    happened to share a post-restart ib_order_id. A no-op if no matching row
    exists.
    """
    order_id = get_order_db_id(ib_order_id, ib_perm_id=ib_perm_id)
    if order_id is None:
        return
    update_order_status(
        order_id, status, filled_quantity, remaining_quantity,
        avg_fill_price, last_fill_price, ib_perm_id, why_held,
    )


def update_order_status_only(order_id: int, status: str, ib_perm_id: Optional[int] = None) -> None:
    """Patch just status/ib_perm_id, leaving fill bookkeeping fields untouched.

    Used by the openOrder() startup/reconnect reconciliation, which reports a
    status but no filled/remaining/avg_fill_price -- those only ever arrive
    via orderStatus()/execDetails(), and writing through update_order_status's
    defaults here would zero out a partially-filled order's progress.
    """
    sql = """
        UPDATE orders SET
            status     = ?,
            ib_perm_id = COALESCE(?, ib_perm_id),
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
    """
    with get_connection() as conn:
        conn.execute(sql, (status, ib_perm_id, order_id))


def get_recorded_net_position(symbol: str) -> float:
    """Net signed shares currently implied by our own recorded fills for `symbol`.

    Sums every recorded execution (BOT positive, SLD negative) -- an
    independent, locally-derived view of what we believe we hold, separate
    from IBKR's own broker-reported position. Used by the daily reconciliation
    check (DEPLOYMENT.md §6.3, `risk/reconciliation.py`) to catch a missed
    fill, a bust, a corporate action, or manual TWS intervention.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE side WHEN 'BOT' THEN shares ELSE -shares END), 0) "
            "FROM executions WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    return row[0]


def update_execution_commission(
    ib_exec_id: str,
    commission: Optional[float] = None,
    commission_currency: Optional[str] = None,
    realized_pnl: Optional[float] = None,
) -> None:
    """Patch commission data onto an existing execution row from a commissionAndFeesReport callback."""
    sql = """
        UPDATE executions SET
            commission          = COALESCE(?, commission),
            commission_currency = COALESCE(?, commission_currency),
            realized_pnl        = COALESCE(?, realized_pnl)
        WHERE ib_exec_id = ?
    """
    with get_connection() as conn:
        conn.execute(sql, (commission, commission_currency, realized_pnl, ib_exec_id))
