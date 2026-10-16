from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import spot_bot.reconcile as reconcile_module
from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)
from spot_bot.order_service import create_intent


class FakeReconcileAdapter:
    def __init__(self):
        self.orders = {
            "buy-ex": {"id": "buy-ex", "status": "closed", "filled": "0.01", "average": "80000"},
            "sell-ex": {"id": "sell-ex", "status": "closed", "filled": "0.01", "average": "81000"},
        }
        self.trades = [
            {"id": "buy-trade-1", "order": "buy-ex", "amount": "0.01", "price": "80000", "cost": "800", "fee": {"cost": "0.08", "currency": "USDT"}},
            {"id": "sell-trade-1", "order": "sell-ex", "amount": "0.01", "price": "81000", "cost": "810", "fee": {"cost": "0.08", "currency": "USDT"}},
        ]

    def fetch_order(self, exchange_order_id, symbol):
        return self.orders[exchange_order_id]

    def fetch_order_by_client_id(self, symbol, client_order_id):
        raise RuntimeError("client lookup should not be needed")

    def recent_trades(self, symbol, limit=100):
        return self.trades


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_order(db, side, exchange_id, price):
    order = create_intent(
        db,
        OrderRequest(
            Environment.TESTNET,
            "BTC/USDT",
            side,
            OrderType.LIMIT,
            D("0.01"),
            D(price),
        ),
    )
    order.exchange_order_id = exchange_id
    order.status = OrderStatus.OPEN.value
    return order


def test_reconcile_twice_does_not_duplicate_inventory_or_realized_pnl(monkeypatch):
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
    buy = make_order(db, OrderSide.BUY, "buy-ex", "80000")
    sell = make_order(db, OrderSide.SELL, "sell-ex", "81000")
    db.commit()

    adapter = FakeReconcileAdapter()
    monkeypatch.setattr(reconcile_module, "BinanceSpot", lambda environment: adapter)

    first = reconcile_module.reconcile_open_orders(db, Environment.TESTNET, "BTC/USDT")
    assert first.checked == 2
    assert first.fills_recorded == 2

    fills = db.scalars(select(SpotFill).order_by(SpotFill.id)).all()
    assert len(fills) == 2
    for fill in fills:
        apply_fill(db, fill)
    db.commit()

    second = reconcile_module.reconcile_open_orders(db, Environment.TESTNET, "BTC/USDT")
    assert second.checked == 0
    assert second.fills_recorded == 0

    fills_after = db.scalars(select(SpotFill).order_by(SpotFill.id)).all()
    applications = db.scalar(select(func.count()).select_from(InventoryApplication))
    pnl_entries = db.scalar(select(func.count()).select_from(RealizedPNLEntry))
    position_after = db.get(InventoryPosition, position.id)

    assert len(fills_after) == 2
    assert applications == 2
    assert pnl_entries == 1
    assert position_after.base_quantity == D("0")
    assert position_after.quote_cost == D("0")

    pnl = db.scalar(select(RealizedPNLEntry))
    assert pnl.quantity == D("0.01")
    assert pnl.entry_cost == D("800.080000000000")
    assert pnl.proceeds == D("809.920000000000")
    assert pnl.realized_pnl == D("9.840000000000")

    for fill in fills_after:
        result = apply_fill(db, fill)
        assert result.applied is False
    db.commit()

    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 2
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1
