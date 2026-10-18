from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

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


def test_partial_sell_preserves_remaining_weighted_cost(db):
    buy = make_fill(db, side="buy", amount="0.004", price="80000", fee="0.32")
    apply_fill(db, buy)
    db.commit()
    sell = make_fill(db, side="sell", amount="0.001", price="84000", fee="0.084")
    result = apply_fill(db, sell)
    db.commit()
    assert result.base_quantity == D("0.003")
    assert result.quote_cost == D("240.24")
    assert result.realized_pnl == D("3.836")
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1


def test_sell_base_fee_consumes_fee_quantity_and_realizes_correct_pnl(db):
    buy = make_fill(db, side="buy", amount="0.002", price="80000")
    apply_fill(db, buy)
    db.commit()
    sell = make_fill(db, side="sell", amount="0.001", price="84000", fee="0.00001", fee_currency="BTC")
    result = apply_fill(db, sell)
    db.commit()
    assert result.base_quantity == D("0.00099")
    assert result.quote_cost == D("79.2")
    assert result.realized_pnl == D("3.2")
    pnl = db.scalar(select(RealizedPNLEntry).where(RealizedPNLEntry.fill_id == sell.id))
    assert pnl is not None
    assert pnl.fees == D("0")


def test_sell_quote_fee_is_removed_from_proceeds(db):
    buy = make_fill(db, side="buy", amount="0.001", price="80000")
    apply_fill(db, buy)
    db.commit()
    sell = make_fill(db, side="sell", amount="0.001", price="84000", fee="0.084", fee_currency="USDT")
    result = apply_fill(db, sell)
    db.commit()
    assert result.base_quantity == D("0")
    assert result.quote_cost == D("0")
    assert result.realized_pnl == D("3.916")
    pnl = db.scalar(select(RealizedPNLEntry).where(RealizedPNLEntry.fill_id == sell.id))
    assert pnl is not None
    assert pnl.fees == D("0.084")
    assert pnl.proceeds == D("83.916")


def test_duplicate_fill_does_not_create_second_realized_pnl(db):
    buy = make_fill(db, side="buy", amount="0.002", price="80000")
    apply_fill(db, buy)
    db.commit()
    sell = make_fill(db, side="sell", amount="0.001", price="84000", fee="0.084")
    first = apply_fill(db, sell)
    db.commit()
    second = apply_fill(db, sell)
    db.commit()
    assert first.applied is True
    assert second.applied is False
    assert second.realized_pnl == first.realized_pnl
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 2


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


def test_third_asset_fee_is_included_in_quote_cost(db):
    fill = make_fill(db, side="buy", amount="0.001", price="80000", fee="0.01", fee_currency="BNB")
    fill.fee_quote_value = D("3")
    result = apply_fill(db, fill)
    db.commit()
    assert result.base_quantity == D("0.001")
    assert result.quote_cost == D("80003")


def test_third_asset_fee_without_quote_valuation_fails_closed(db):
    fill = make_fill(db, side="buy", amount="0.001", price="80000", fee="0.01", fee_currency="BNB")
    with pytest.raises(RuntimeError, match="third-asset fee has no quote-currency valuation"):
        apply_fill(db, fill)
    db.rollback()
    assert db.scalar(select(InventoryApplication).where(InventoryApplication.fill_id == fill.id)) is None


def test_third_asset_fee_is_deducted_from_sell_proceeds_and_pnl(db):
    buy = make_fill(db, side="buy", amount="0.001", price="80000")
    apply_fill(db, buy)
    db.commit()
    sell = make_fill(db, side="sell", amount="0.001", price="84000", fee="0.01", fee_currency="BNB")
    sell.fee_quote_value = D("3")
    result = apply_fill(db, sell)
    db.commit()
    pnl = db.scalar(select(RealizedPNLEntry).where(RealizedPNLEntry.fill_id == sell.id))
    assert result.realized_pnl == D("1")
    assert pnl.proceeds == D("81")
    assert pnl.fees == D("3")
