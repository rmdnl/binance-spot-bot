from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

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
from spot_bot.reconcile import reconcile_open_orders


class DelayedTradeAdapter:
    def __init__(self):
        self.trades = [
            {
                "id": "late-trade-001",
                "order": "exchange-canceled-partial",
                "amount": "0.005",
                "price": "80000",
                "cost": "400",
                "fee": {"cost": "0.04", "currency": "USDT"},
            }
        ]
        self.trade_lookups = 0

    def recent_trades(self, symbol, limit=100):
        self.trade_lookups += 1
        return self.trades


def test_terminal_partial_fill_is_reconciled_when_trade_history_arrives_late(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    adapter = DelayedTradeAdapter()
    monkeypatch.setattr("spot_bot.reconcile.BinanceSpot", lambda _environment: adapter)

    with Session(engine) as db:
        position = InventoryPosition(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            base_quantity=D("0"),
            quote_cost=D("0"),
        )
        order = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            order_type="limit",
            status=OrderStatus.CANCELED.value,
            amount=D("0.01"),
            filled_amount=D("0.005"),
            price=D("80000"),
            client_order_id="late-fill-client-001",
            exchange_order_id="exchange-canceled-partial",
        )
        db.add_all([position, order])
        db.commit()

        # First reconciliation after cancellation discovers the delayed trade.
        result = reconcile_open_orders(db, Environment.TESTNET)
        assert result.checked == 1
        assert result.changed == 0
        assert result.fills_recorded == 1
        assert order.status == OrderStatus.CANCELED.value
        assert position.base_quantity == D("0.005000000000")
        assert position.quote_cost == D("400.040000000000")
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 1
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0

        # Repeated reconciliation is idempotent and retains the terminal state.
        result = reconcile_open_orders(db, Environment.TESTNET)
        assert result.checked == 0
        assert result.fills_recorded == 0
        assert position.base_quantity == D("0.005000000000")
        assert position.quote_cost == D("400.040000000000")
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 1
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0

    engine.dispose()
