from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from spot_bot.models import Base, Environment, InventoryPosition, RealizedPNLEntry, SpotFill, SpotOrder
from spot_bot.pnl_service import record_realized_pnl, unrealized_pnl


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


def make_position(db, qty="0.002", cost="162.162"):
    position = InventoryPosition(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        base_quantity=D(qty),
        quote_cost=D(cost),
    )
    db.add(position)
    db.flush()
    return position


def make_sell_fill(db, position, amount="0.001", price="84000", fee="0.084"):
    order = SpotOrder(
        environment=position.environment,
        symbol=position.symbol,
        side="sell",
        order_type="limit",
        status="filled",
        amount=D(amount),
        price=D(price),
        filled_amount=D(amount),
    )
    db.add(order)
    db.flush()
    fill = SpotFill(
        order_id=order.id,
        symbol=position.symbol,
        side="sell",
        price=D(price),
        amount=D(amount),
        quote_amount=D(amount) * D(price),
        fee=D(fee),
        fee_currency="USDT",
        exchange_trade_id=f"trade-{order.id}",
        timestamp=datetime.now(UTC),
    )
    db.add(fill)
    db.flush()
    return fill


def test_realized_pnl_records_weighted_cost_and_is_idempotent(db):
    position = make_position(db)
    fill = make_sell_fill(db, position)

    first = record_realized_pnl(db, fill)
    second = record_realized_pnl(db, fill)

    assert first.recorded is True
    assert second.recorded is False
    assert first.quantity == D("0.001")
    assert first.entry_cost == D("81.081000000000")
    assert first.proceeds == D("83.916000000000")
    assert first.fees == D("0.084000000000")
    assert first.realized_pnl == D("2.835000000000")
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1


def test_realized_pnl_rejects_non_sell(db):
    position = make_position(db)
    order = SpotOrder(
        environment=position.environment,
        symbol=position.symbol,
        side="buy",
        order_type="limit",
        status="filled",
        amount=D("0.001"),
        price=D("80000"),
        filled_amount=D("0.001"),
    )
    db.add(order)
    db.flush()
    fill = SpotFill(
        order_id=order.id,
        symbol=position.symbol,
        side="buy",
        price=D("80000"),
        amount=D("0.001"),
        quote_amount=D("80"),
        timestamp=datetime.now(UTC),
    )
    db.add(fill)
    db.flush()
    with pytest.raises(ValueError, match="sell fills"):
        record_realized_pnl(db, fill)


def test_unrealized_pnl_uses_remaining_cost(db):
    position = make_position(db, qty="0.001", cost="81.081")
    assert unrealized_pnl(position, D("85000")) == D("3.919000000000")


def test_realized_pnl_rejects_oversell(db):
    position = make_position(db, qty="0.001", cost="81.081")
    fill = make_sell_fill(db, position, amount="0.002", price="84000", fee="0.168")
    with pytest.raises(ValueError, match="exceeds available inventory"):
        record_realized_pnl(db, fill)
