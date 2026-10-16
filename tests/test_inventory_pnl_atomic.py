from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


def make_position(db):
    position = InventoryPosition(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        base_quantity=D("0.002"),
        quote_cost=D("162.162"),
    )
    db.add(position)
    db.flush()
    return position


def make_sell(db, amount="0.001"):
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="sell",
        order_type="limit",
        status="filled",
        amount=D(amount),
        price=D("84000"),
        filled_amount=D(amount),
    )
    db.add(order)
    db.flush()
    fill = SpotFill(
        order_id=order.id,
        symbol="BTC/USDT",
        side="sell",
        price=D("84000"),
        amount=D(amount),
        quote_amount=D(amount) * D("84000"),
        fee=D(amount) * D("84"),
        fee_currency="USDT",
        exchange_trade_id=f"trade-{order.id}",
        timestamp=datetime.now(UTC),
    )
    db.add(fill)
    db.flush()
    return fill


def test_inventory_and_pnl_commit_together(db):
    position = make_position(db)
    fill = make_sell(db)

    result = apply_fill(db, fill)
    db.commit()

    assert result.realized_pnl == D("2.835000000000")
    assert db.scalar(select(InventoryPosition.base_quantity).where(InventoryPosition.id == position.id)) == D("0.001000000000")
    assert db.scalar(select(InventoryPosition.quote_cost).where(InventoryPosition.id == position.id)) == D("81.081000000000")
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1


def test_failed_application_rolls_back_marker_inventory_and_pnl(db):
    position = make_position(db)
    fill = make_sell(db, amount="0.003")

    with pytest.raises(ValueError, match="exceeds available inventory"):
        apply_fill(db, fill)
    db.rollback()

    assert db.scalar(select(InventoryPosition.base_quantity).where(InventoryPosition.id == position.id)) == D("0.002000000000")
    assert db.scalar(select(InventoryPosition.quote_cost).where(InventoryPosition.id == position.id)) == D("162.162000000000")
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 0
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0


def test_duplicate_fill_does_not_double_account(db):
    make_position(db)
    fill = make_sell(db)

    first = apply_fill(db, fill)
    second = apply_fill(db, fill)
    db.commit()

    assert first.applied is True
    assert second.applied is False
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1


def test_pnl_and_inventory_rollback_on_later_constraint_failure(db):
    position = make_position(db)
    fill = make_sell(db)
    apply_fill(db, fill)
    db.flush()

    # Force the same unique fill_id constraint that protects PNL idempotency.
    db.add(RealizedPNLEntry(
        fill_id=fill.id,
        environment=fill.order.environment,
        symbol=fill.symbol,
        quantity=D("1"),
        entry_cost=D("1"),
        proceeds=D("1"),
        fees=D("0"),
        realized_pnl=D("0"),
        timestamp=fill.timestamp,
    ))
    with pytest.raises(Exception):
        db.flush()
    db.rollback()

    assert db.scalar(select(InventoryPosition.base_quantity).where(InventoryPosition.id == position.id)) == D("0.002000000000")
    assert db.scalar(select(InventoryPosition.quote_cost).where(InventoryPosition.id == position.id)) == D("162.162000000000")
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 0
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0
