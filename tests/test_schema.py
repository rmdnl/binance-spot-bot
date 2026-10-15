import json

from sqlalchemy import create_engine, inspect, text

from dca_bot.db import Base
from dca_bot.schema import migrate_schema


def test_schema_creates_durable_dca_tables():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)

    tables = set(inspect(engine).get_table_names())
    assert {"paper_deals", "deal_orders", "deal_events", "deal_accounting", "strategy_risk_states"} <= tables

    order_columns = {column["name"] for column in inspect(engine).get_columns("deal_orders")}
    assert {"deal_id", "sequence", "order_kind", "side", "price", "base_quantity", "quote_amount", "fee_quote"} <= order_columns

    event_columns = {column["name"] for column in inspect(engine).get_columns("deal_events")}
    assert {"deal_id", "event_type", "price", "quote_amount", "base_quantity", "pnl_quote"} <= event_columns

    accounting_columns = {column["name"] for column in inspect(engine).get_columns("deal_accounting")}
    assert {"deal_id", "quote_asset", "quote_invested", "quote_proceeds", "realized_pnl_quote", "unrealized_pnl_quote"} <= accounting_columns


def test_schema_migrates_legacy_paper_deal_columns_and_is_idempotent():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL, config_json TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME, updated_at DATETIME)"))
        connection.execute(text("CREATE TABLE paper_deals (id VARCHAR(36) PRIMARY KEY, strategy_id INTEGER NOT NULL, symbol VARCHAR(30) NOT NULL, status VARCHAR(16) NOT NULL, state_json TEXT NOT NULL, created_at DATETIME, updated_at DATETIME)"))
        payload = {
            "id": "deal-1",
            "symbol": "ETH/BTC",
            "status": "active",
            "started_at_epoch": 1700000000,
            "base_price": "0.05",
            "last_price": "0.049",
            "quote_spent": "0.001",
            "base_quantity": "0.02",
            "entry_fees_quote": "0.000001",
            "exit_price": None,
            "realized_pnl_quote": "0",
            "close_reason": None,
        }
        connection.execute(
            text("INSERT INTO strategies(id,name,config_json,enabled) VALUES (1,'DCA', '{}', 1)")
        )
        connection.execute(
            text("INSERT INTO paper_deals(id,strategy_id,symbol,status,state_json,created_at,updated_at) VALUES (:id,1,:symbol,:status,:state_json,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
            {"id": "deal-1", "symbol": "ETH/BTC", "status": "active", "state_json": json.dumps(payload)},
        )

    migrate_schema(engine)
    migrate_schema(engine)

    with engine.connect() as connection:
        row = connection.execute(text("SELECT base_asset, quote_asset, base_price, last_price, quote_spent, base_quantity, entry_fees_quote, version FROM paper_deals WHERE id='deal-1'")).mappings().one()
        assert row["base_asset"] == "ETH"
        assert row["quote_asset"] == "BTC"
        assert str(row["base_price"]) == "0.05"
        assert str(row["last_price"]) == "0.049"
        assert str(row["quote_spent"]) == "0.001"
        assert str(row["base_quantity"]) == "0.02"
        assert str(row["entry_fees_quote"]) == "0.000001"
        assert row["version"] == 1

        versions = connection.execute(text("SELECT version FROM schema_migrations")).scalars().all()
        assert versions == [1]
