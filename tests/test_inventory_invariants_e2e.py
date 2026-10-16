from decimal import Decimal as D

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


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_fill(db, side, amount, price, fee="0.08", trade_id="trade-1"):
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side=side,
        order_type="limit",
        status="filled",
        amount=D(amount),
        price=D(price),
        client_order_id=f"invariant-{trade_id}",
        exchange_order_id=f"exchange-{trade_id}",
    )
    db.add(order)
    db.flush()
    fill = SpotFill(
        order_id=order.id,
        exchange_trade_id=trade_id,
        symbol="BTC/USDT",
        side=side,
        amount=D(amount),
        price=D(price),
        quote_amount=D(amount) * D(price),
        fee=D(fee),
        fee_currency="USDT",
    )
    db.add(fill)
    db.commit()
    return fill


def counts(db):
    return {
        "fills": db.scalar(select(func.count()).select_from(SpotFill)),
        "applications": db.scalar(select(func.count()).select_from(InventoryApplication)),
        "pnl": db.scalar(select(func.count()).select_from(RealizedPNLEntry)),
    }


def test_repeated_apply_fill_is_idempotent_and_preserves_inventory_invariant():
    db = db_session()
    position = InventoryPosition(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        base_quantity=D("0"),
        quote_cost=D("0"),
    )
    db.add(position)
    db.commit()

    buy = make_fill(db, "buy", "0.01", "80000", trade_id="buy-1")
    first = apply_fill(db, buy)
    db.commit()
    snapshot = counts(db), position.base_quantity, position.quote_cost

    second = apply_fill(db, buy)
    db.commit()
    assert second.applied is False
    assert counts(db) == snapshot[0]
    assert position.base_quantity == snapshot[1]
    assert position.quote_cost == snapshot[2]


def test_sell_realizes_pnl_once_after_restart_like_replay():
    db = db_session()
    position = InventoryPosition(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        base_quantity=D("0"),
        quote_cost=D("0"),
    )
    db.add(position)
    db.commit()

    buy = make_fill(db, "buy", "0.01", "80000", trade_id="buy-2")
    assert apply_fill(db, buy).applied is True
    db.commit()

    sell = make_fill(db, "sell", "0.01", "81000", trade_id="sell-2")
    first = apply_fill(db, sell)
    db.commit()
    assert first.applied is True
    assert first.realized_pnl == D("9.84")
    before = counts(db)

    replay = apply_fill(db, sell)
    db.commit()
    assert replay.applied is False
    assert counts(db) == before
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1
    assert position.base_quantity == D("0")
    assert position.quote_cost == D("0")
