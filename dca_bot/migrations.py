from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


SCHEMA_VERSION = 2


def ensure_schema(engine: Engine) -> None:
    """Apply small, idempotent SQLite schema upgrades before ORM startup.

    Existing installations are preserved. New ORM tables are still created by
    ``Base.metadata.create_all``; this guard only handles schema-version state
    and additive columns that cannot be expressed by create_all on an existing
    SQLite table.
    """
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as conn:
        inspector = inspect(conn)
        if "schema_meta" not in inspector.get_table_names():
            conn.execute(text("CREATE TABLE schema_meta (version INTEGER NOT NULL)"))
            conn.execute(text("INSERT INTO schema_meta(version) VALUES (1)"))

        current = conn.execute(text("SELECT version FROM schema_meta LIMIT 1")).scalar_one()

        if current < 2:
            # Version 2 introduces exchange_orders through ORM create_all. Keep
            # this migration additive so existing paper data is untouched.
            conn.execute(text("UPDATE schema_meta SET version = 2"))


def schema_version(engine: Engine) -> int:
    if engine.dialect.name != "sqlite":
        return SCHEMA_VERSION
    inspector = inspect(engine)
    if "schema_meta" not in inspector.get_table_names():
        return 0
    with engine.connect() as conn:
        value = conn.execute(text("SELECT version FROM schema_meta LIMIT 1")).scalar()
    return int(value or 0)
