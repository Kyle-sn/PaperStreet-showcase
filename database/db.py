import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "paperstreet.db"
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_db_path() -> Path:
    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_db() -> None:
    """Create all tables if they don't already exist, then self-heal stored data."""
    # Column migrations run BEFORE the schema because schema.sql creates indexes
    # that reference columns added by these migrations (e.g. orders.trade_group_id).
    # On a fresh DB the tables don't exist yet, so the migration raises — that's
    # fine; the schema will create them with the columns already present.
    from .trading import migrate_orders_trade_group
    try:
        migrate_orders_trade_group()
    except Exception:
        pass

    schema = _SCHEMA_PATH.read_text()
    with get_connection() as conn:
        conn.executescript(schema)

    from .market_data import migrate_bar_datetimes
    migrate_bar_datetimes()
