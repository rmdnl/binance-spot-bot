from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_exchange_order_columns(engine: Engine) -> None:
    """Additive upgrade for existing SQLite installations.

    SQLite create_all() does not alter existing tables. Keep this migration
    deliberately narrow and preserve all existing rows.
    """
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as conn:
        tables = inspect(conn).get_table_names()
        if "exchange_orders" not in tables:
            return
        columns = {c["name"] for c in inspect(conn).get_columns("exchange_orders")}
        if "last_accounted_filled" not in columns:
            conn.execute(text("ALTER TABLE exchange_orders ADD COLUMN last_accounted_filled VARCHAR(64) NOT NULL DEFAULT '0'"))
