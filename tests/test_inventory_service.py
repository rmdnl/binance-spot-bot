from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import Base, Environment, InventoryApplication, InventoryPosition, SpotFill, SpotOrder

D = Decimal


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_fill(db: Session, *, side: str, amount: str, price: str, fee: str = "0", fee_currency: str = "USDT") -> SpotFill:
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side=side,
        order_type="limit",
        status="filled",
        amount=D(amount),
        price=D(price),
    )
    db.add(order)
    db.flush()
    fill = SpotFill(
        order_id=order.id,
        symbol="BTC/USDT",
        side=side,
        price=D(price),
        amount=D(amount),
        quote_amount=D(amount) * D(price),
        fee=D(fee),
        fee_currency=fee_currency,
        exchange_trade_id=f"trade-{order.id}",
    )
    db.add(fill)
    db.flush()
    return fill


def test_buy_adds_inventory_and_quote_cost(db):
    fill = make_fill(db, side="buy", amount="0.002", price="80000", fee="0.16")

    result = apply_fill(db, fill)
    db.commit()

    assert result.applied is True
    assert result.base_quantity == D("0.002")
    assert result.quote_cost == D("160.16")


def test_buy_base_fee_reduces_received_inventory(db):
    fill = make_fill(db, side="buy", amount="0.002", price="80000", fee="0.00001", fee_currency="BTC")

    result = apply_fill(db, fill)
    db.commit()

    assert result.base_quantity == D("0.00199")
    assert result.quote_cost == D("160")


def test_multiple_buys_use_accumulated_cost(db):
    first = make_fill(db, side="buy", amount="0.001", price="80000", fee="0.08")
    apply_fill(db, first)
    db.commit()

    second = make_fill(db, side="buy", amount="0.001", price="82000", fee="0.082")
    result = apply_fill(db, second)
    db.commit()

    assert result.base_quantity == D("0.002")
    assert result.quote_cost == D("162.162")


def test_sell_releases_weighted_average_cost_and_realizes_pnl(db):
    first = make_fill(db, side="buy", amount="0.001", price="80000", fee="0.08")
    apply_fill(db, first)
    db.commit()

    second = make_fill(db, side="buy", amount="0.001", price="82000", fee="0.082")
    apply_fill(db, second)
    db.commit()

    sell = make_fill(db, side="sell", amount="0.001", price="84000", fee="0.084")
    result = apply_fill(db, sell)
    db.commit()

    assert result.base_quantity == D("0.001")
    assert result.quote_cost == D("81.081")
    assert result.realized_pnl == D("2.835")


def test_duplicate_fill_is_idempotent(db):
    fill = make_fill(db, side="buy", amount="0.002", price="80000", fee="0.16")
    first = apply_fill(db, fill)
    db.commit()

    second = apply_fill(db, fill)
    db.commit()

    assert first.applied is True
    assert second.applied is False
    position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
    assert position.base_quantity == D("0.002")
    assert position.quote_cost == D("160.16")
    assert db.scalar(select(InventoryApplication).where(InventoryApplication.fill_id == fill.id)) is not None


def test_sell_exceeding_inventory_rolls_back_application_marker_and_mutation(db):
    buy = make_fill(db, side="buy", amount="0.001", price="80000")
    apply_fill(db, buy)
    db.commit()

    sell = make_fill(db, side="sell", amount="0.002", price="84000")
    with pytest.raises(ValueError, match="exceeds available inventory"):
        apply_fill(db, sell)
        db.commit()
    db.rollback()

    position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
    assert position.base_quantity == D("0.001")
    assert position.quote_cost == D("80")
    assert db.scalar(select(InventoryApplication).where(InventoryApplication.fill_id == sell.id)) is None


def test_zero_and_negative_values_are_rejected(db):
    zero = make_fill(db, side="buy", amount="0", price="80000")
    with pytest.raises(ValueError, match="fill amount"):
        apply_fill(db, zero)

    negative_fee = make_fill(db, side="buy", amount="0.001", price="80000", fee="-0.01")
    with pytest.raises(ValueError, match="fee"):
        apply_fill(db, negative_fee)
