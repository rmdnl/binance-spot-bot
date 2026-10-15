from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

SCHEMA_VERSION = 1


def _add_column_if_missing(connection, table: str, column: str, ddl: str) -> None:
    columns = {item["name"] for item in inspect(connection).get_columns(table)}
    if column not in columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _dec(value: object, default: str = "0") -> str:
    try:
        return str(Decimal(str(value if value is not None else default)))
    except Exception:
        return default


def _backfill_deal_columns(connection) -> None:
    rows = connection.execute(text("SELECT id, symbol, status, state_json, created_at, updated_at FROM paper_deals")).mappings().all()
    for row in rows:
        try:
            data = json.loads(row["state_json"])
        except Exception:
            continue

        symbol = str(data.get("symbol") or row["symbol"] or "").strip().upper().replace("-", "/")
        base_asset, _, quote_asset = symbol.partition("/")
        status = str(data.get("status") or row["status"] or "active")
        started_at = row["created_at"]
        if data.get("started_at_epoch") is not None:
            try:
                started_at = datetime.fromtimestamp(float(data["started_at_epoch"]), tz=timezone.utc)
            except Exception:
                pass
        closed_at = row["updated_at"] if status == "closed" else None

        connection.execute(
            text("""
                UPDATE paper_deals
                SET base_asset = :base_asset,
                    quote_asset = :quote_asset,
                    base_price = :base_price,
                    last_price = :last_price,
                    quote_spent = :quote_spent,
                    base_quantity = :base_quantity,
                    entry_fees_quote = :entry_fees_quote,
                    exit_price = :exit_price,
                    realized_pnl_quote = :realized_pnl_quote,
                    close_reason = :close_reason,
                    started_at = :started_at,
                    closed_at = :closed_at,
                    version = 1
                WHERE id = :id
            """),
            {
                "id": row["id"],
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "base_price": _dec(data.get("base_price")),
                "last_price": _dec(data.get("last_price")),
                "quote_spent": _dec(data.get("quote_spent")),
                "base_quantity": _dec(data.get("base_quantity")),
                "entry_fees_quote": _dec(data.get("entry_fees_quote")),
                "exit_price": data.get("exit_price"),
                "realized_pnl_quote": _dec(data.get("realized_pnl_quote")),
                "close_reason": data.get("close_reason"),
                "started_at": started_at,
                "closed_at": closed_at,
            },
        )


def migrate_schema(engine: Engine) -> None:
    """Apply the small, idempotent schema migration used by this deployment.

    The project intentionally does not introduce Alembic yet. Existing SQLite
    installations therefore get additive migrations here, while new databases
    are created from SQLAlchemy metadata. Each migration is recorded so startup
    never repeats a completed data backfill.
    """
    from . import models  # noqa: F401

    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        ))
        applied = {
            int(row[0])
            for row in connection.execute(text("SELECT version FROM schema_migrations")).all()
        }
        if SCHEMA_VERSION in applied:
            return

        # These columns make the deal header queryable without deserializing JSON.
        # Defaults are required for SQLite's ALTER TABLE ADD COLUMN semantics.
        for column, ddl in (
            ("base_asset", "VARCHAR(20) NOT NULL DEFAULT ''"),
            ("quote_asset", "VARCHAR(20) NOT NULL DEFAULT ''"),
            ("base_price", "NUMERIC(38,18) NOT NULL DEFAULT 0"),
            ("last_price", "NUMERIC(38,18) NOT NULL DEFAULT 0"),
            ("quote_spent", "NUMERIC(38,18) NOT NULL DEFAULT 0"),
            ("base_quantity", "NUMERIC(38,18) NOT NULL DEFAULT 0"),
            ("entry_fees_quote", "NUMERIC(38,18) NOT NULL DEFAULT 0"),
            ("exit_price", "NUMERIC(38,18)"),
            ("realized_pnl_quote", "NUMERIC(38,18) NOT NULL DEFAULT 0"),
            ("close_reason", "VARCHAR(80)"),
            ("started_at", "DATETIME"),
            ("closed_at", "DATETIME"),
            ("version", "INTEGER NOT NULL DEFAULT 1"),
        ):
            _add_column_if_missing(connection, "paper_deals", column, ddl)

        _backfill_deal_columns(connection)
        connection.execute(
            text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
            {"version": SCHEMA_VERSION, "applied_at": datetime.now(timezone.utc).isoformat()},
        )
