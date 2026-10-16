from decimal import Decimal as D

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, InventoryPosition


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def assert_balance_matches_inventory(exchange, position):
    assert D(str(exchange["base"])) == position.base_quantity
    assert D(str(exchange["quote_cost"])) == position.quote_cost


def test_balance_reconciliation_accepts_exact_exchange_snapshot():
    engine = make_db()
    with Session(engine) as db:
        position = InventoryPosition(environment=Environment.TESTNET.value, symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT", base_quantity=D("0.01"), quote_cost=D("400.08"))
        db.add(position)
        db.commit()
        exchange = {"base": "0.01", "quote_cost": "400.08"}
        assert_balance_matches_inventory(exchange, position)


def test_balance_reconciliation_detects_inventory_drift_after_restart():
    engine = make_db()
    with Session(engine) as db:
        position = InventoryPosition(environment=Environment.TESTNET.value, symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT", base_quantity=D("0.01"), quote_cost=D("400.08"))
        db.add(position)
        db.commit()

    with Session(engine) as db:
        position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
        exchange = {"base": "0.009", "quote_cost": "400.08"}
        try:
            assert_balance_matches_inventory(exchange, position)
        except AssertionError:
            drift_detected = True
        else:
            drift_detected = False
        assert drift_detected is True
