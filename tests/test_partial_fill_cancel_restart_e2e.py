from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    OrderStatus,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)
from spot_bot.order_service import cancel_order, record_fills, reconcile_order


class PartialCancelAdapter:
    def __init__(self):
        self.order = {
            "id": "exchange-pc-1",
            "status": "open",
            "filled": "0.005",
            "average": "80000",
            "fee": {"cost": "0.04", "currency": "USDT"},
        }
        self.trades = [
            {
                "id": "trade-pc-1",
                "order": "exchange-pc-1",
                "amount": "0.005",
                "price": "80000",
                "cost": "400",
                "fee": {"cost": "0.04", "currency": "USDT"},
            }
        ]
        self.cancel_calls = 0
        self.lookup_calls = 0

    def fetch_order(self, exchange_order_id, symbol):
        assert exchange_order_id == self.order["id"]
        self.lookup_calls += 1
        return dict(self.order)

    def fetch_order_by_client_id(self, symbol, client_order_id):
        self.lookup_calls += 1
        return dict(self.order)

    def cancel_order(self, exchange_order_id, symbol):
        self.cancel_calls += 1
        self.order["status"] = "canceled"
        return dict(self.order)

    def recent_trades(self, symbol, limit=100):
        return list(self.trades)


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def counts(db):
    return {
        "fills": db.scalar(select(func.count()).select_from(SpotFill)),
        "applications": db.scalar(select(func.count()).select_from(InventoryApplication)),
        "pnl": db.scalar(select(func.count()).select_from(RealizedPNLEntry)),
    }


def test_partial_fill_cancel_restart_reconcile_and_accounting_are_idempotent():
    db = db_session()
    db.add(
        InventoryPosition(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            base_quantity=D("0"),
            quote_cost=D("0"),
        )
    )
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status=OrderStatus.OPEN.value,
        amount=D("0.01"),
        price=D("80000"),
        filled_amount=D("0.005"),
        average_price=D("80000"),
        client_order_id="partial-cancel-restart-001",
        exchange_order_id="exchange-pc-1",
    )
    db.add(order)
    db.commit()
    adapter = PartialCancelAdapter()

    # Cancellation wins for the order state, but the exchange fill remains canonical.
    cancel_order(db, adapter, order)
    db.commit()
    assert order.status == OrderStatus.CANCELED.value
    assert order.filled_amount == D("0.005")
    assert counts(db) == {"fills": 1, "applications": 0, "pnl": 0}

    # Simulate restart: terminal order is not submitted again, and reconciliation is safe.
    restarted = db.get(SpotOrder, order.id)
    assert restarted.status == OrderStatus.CANCELED.value
    assert record_fills(db, adapter, restarted) == 0
    db.commit()
    assert counts(db) == {"fills": 1, "applications": 0, "pnl": 0}

    fill = db.scalar(select(SpotFill).where(SpotFill.order_id == restarted.id))
    result = apply_fill(db, fill)
    db.commit()
    assert result.applied is True
    assert result.realized_pnl == D("0")

    # A later reconcile/replay must not create another fill or accounting application.
    assert record_fills(db, adapter, restarted) == 0
    replay = apply_fill(db, fill)
    db.commit()
    assert replay.applied is False
    assert counts(db) == {"fills": 1, "applications": 1, "pnl": 0}

    position = db.scalar(
        select(InventoryPosition).where(
            InventoryPosition.environment == Environment.TESTNET.value,
            InventoryPosition.symbol == "BTC/USDT",
        )
    )
    assert position.base_quantity == D("0.005000000000")
    assert position.quote_cost == D("400.040000000000")
    assert adapter.cancel_calls == 1


def test_restart_after_partial_fill_cancel_does_not_reopen_terminal_order():
    db = db_session()
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status=OrderStatus.CANCELED.value,
        amount=D("0.01"),
        price=D("80000"),
        filled_amount=D("0.005"),
        client_order_id="partial-cancel-restart-002",
        exchange_order_id="exchange-pc-2",
    )
    db.add(order)
    db.commit()

    adapter = PartialCancelAdapter()
    adapter.order["id"] = "exchange-pc-2"
    adapter.order["status"] = "canceled"
    adapter.order["filled"] = "0.005"

    # Terminal local state must not be passed through active-order reconciliation.
    reconciled = reconcile_order(db, adapter, order)
    assert reconciled.status == OrderStatus.CANCELED.value
    assert adapter.lookup_calls == 0
