from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import Base, Environment, InventoryApplication, InventoryPosition, RealizedPNLEntry, SpotFill, SpotOrder


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def seed(engine):
    with Session(engine) as db:
        position = InventoryPosition(environment=Environment.TESTNET.value, symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT", base_quantity=D("0"), quote_cost=D("0"))
        order = SpotOrder(environment=Environment.TESTNET.value, symbol="BTC/USDT", side="buy", order_type="limit", status="filled", amount=D("0.01"), price=D("80000"), client_order_id="atomicity-1", exchange_order_id="exchange-atomic-1")
        db.add_all([position, order])
        db.flush()
        fill = SpotFill(order_id=order.id, exchange_trade_id="trade-atomic-1", symbol="BTC/USDT", side="buy", amount=D("0.01"), price=D("80000"), quote_amount=D("800"), fee=D("0.08"), fee_currency="USDT")
        db.add(fill)
        db.commit()
        return fill.id


def test_crash_before_commit_leaves_no_partial_accounting_and_restart_applies_once():
    engine = make_db()
    fill_id = seed(engine)

    try:
        with Session(engine) as db:
            fill = db.get(SpotFill, fill_id)
            apply_fill(db, fill)
            raise RuntimeError("simulated process crash before commit")
    except RuntimeError:
        pass

    with Session(engine) as db:
        position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
        assert position.base_quantity == D("0")
        assert position.quote_cost == D("0")
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 0
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0

        fill = db.get(SpotFill, fill_id)
        result = apply_fill(db, fill)
        db.commit()
        assert result.applied is True

    with Session(engine) as db:
        position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
        assert position.base_quantity == D("0.01")
        assert position.quote_cost == D("800.08")
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0


def test_crash_after_accounting_then_rollback_does_not_duplicate_on_restart():
    engine = make_db()
    fill_id = seed(engine)

    with Session(engine) as db:
        fill = db.get(SpotFill, fill_id)
        apply_fill(db, fill)
        db.rollback()

    with Session(engine) as db:
        fill = db.get(SpotFill, fill_id)
        result = apply_fill(db, fill)
        db.commit()
        assert result.applied is True
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1

        result = apply_fill(db, fill)
        db.commit()
        assert result.applied is False
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
