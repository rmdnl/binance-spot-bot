from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    OrderStatus,
    SpotFill,
    SpotOrder,
)
from spot_bot.order_service import record_fills


class OrderScopedTradeAdapter:
    def __init__(self):
        self.order_queries = []
        self.recent_queries = 0

    def trades_for_order(self, symbol, exchange_order_id):
        self.order_queries.append((symbol, exchange_order_id))
        # Represents an old fill that is no longer in the account's latest
        # 100 trades, but is still returned by Binance's orderId filter.
        return [{
            "id": "delayed-old-trade-1",
            "order": exchange_order_id,
            "amount": "0.01",
            "price": "80000",
            "cost": "800",
            "fee": {"cost": "0.08", "currency": "USDT"},
            "timestamp": 1792281600000,
        }]

    def recent_trades(self, symbol, limit=100):
        self.recent_queries += 1
        return []


def test_terminal_order_reconcile_queries_delayed_trades_by_order_id():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    adapter = OrderScopedTradeAdapter()

    with Session(engine) as db:
        order = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            order_type="limit",
            status=OrderStatus.CANCELED.value,
            amount=D("0.02"),
            filled_amount=D("0.01"),
            price=D("80000"),
            client_order_id="delayed-order-scoped-client",
            exchange_order_id="123456789",
        )
        db.add(order)
        db.commit()

        assert record_fills(db, adapter, order) == 1
        db.commit()

        fill = db.scalar(select(SpotFill))
        position = db.scalar(select(InventoryPosition))
        assert fill is not None
        assert fill.exchange_trade_id == "delayed-old-trade-1"
        assert position is not None
        assert position.base_quantity == D("0.010000000000")
        assert position.quote_cost == D("800.080000000000")
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert adapter.order_queries == [("BTC/USDT", "123456789")]
        assert adapter.recent_queries == 0

        # A restart/replay cannot create a second fill or inventory application.
        assert record_fills(db, adapter, order) == 0
        db.commit()
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 1
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert position.base_quantity == D("0.010000000000")
        assert position.quote_cost == D("800.080000000000")

    engine.dispose()


def test_third_asset_fee_uses_historical_price_and_exchange_fill_timestamp():
    from datetime import UTC, datetime

    class HistoricalFeeAdapter(OrderScopedTradeAdapter):
        def trades_for_order(self, symbol, exchange_order_id):
            return [{
                "id": "historical-fee-trade",
                "order": exchange_order_id,
                "amount": "0.01",
                "price": "80000",
                "cost": "800",
                "fee": {"cost": "0.01", "currency": "BNB"},
                "timestamp": 1792281600000,
            }]

        def historical_price(self, symbol, timestamp):
            assert symbol == "BNB/USDT"
            assert timestamp == datetime.fromtimestamp(1792281600, UTC)
            return D("300")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    adapter = HistoricalFeeAdapter()
    with Session(engine) as db:
        order = SpotOrder(
            environment=Environment.TESTNET.value, symbol="BTC/USDT", side="buy",
            order_type="limit", status=OrderStatus.CANCELED.value, amount=D("0.01"),
            filled_amount=D("0.01"), price=D("80000"), client_order_id="historical-fee-client",
            exchange_order_id="historical-fee-order",
        )
        db.add(order)
        db.commit()
        assert record_fills(db, adapter, order) == 1
        db.commit()
        fill = db.scalar(select(SpotFill))
        position = db.scalar(select(InventoryPosition))
        assert fill.timestamp == datetime.fromtimestamp(1792281600, UTC).replace(tzinfo=None)
        assert fill.fee_quote_value == D("3.000000000000")
        assert position.quote_cost == D("803.000000000000")
    engine.dispose()
