from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    SpotFill,
    SpotOrder,
)
from spot_bot.order_service import record_fills


class TradeIdentityAdapter:
    def __init__(self, exchange_order_id):
        self.exchange_order_id = exchange_order_id

    def recent_trades(self, symbol, limit=100):
        return [
            {
                "id": "trade-1",
                "order": self.exchange_order_id,
                "amount": "0.01",
                "price": "100",
                "cost": "1",
                "fee": {"cost": "0", "currency": "USDT"},
            }
        ]


def test_trade_identity_is_scoped_to_environment_and_symbol():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    scenarios = [
        (Environment.TESTNET.value, "BTC/USDT", "exchange-testnet-btc"),
        (Environment.TESTNET.value, "ETH/USDT", "exchange-testnet-eth"),
        (Environment.LIVE.value, "BTC/USDT", "exchange-live-btc"),
    ]

    with Session(engine) as db:
        orders = []
        for environment, symbol, exchange_order_id in scenarios:
            order = SpotOrder(
                environment=environment,
                symbol=symbol,
                side="buy",
                order_type="limit",
                status="filled",
                amount=D("0.01"),
                price=D("100"),
                client_order_id=f"identity-{environment}-{symbol}",
                exchange_order_id=exchange_order_id,
            )
            db.add(order)
            orders.append(order)
        db.commit()

        for order in orders:
            assert record_fills(
                db,
                TradeIdentityAdapter(order.exchange_order_id),
                order,
            ) == 1
            db.commit()

        assert db.scalar(select(func.count()).select_from(SpotFill)) == 3
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 3

        for environment, symbol, _ in scenarios:
            position = db.scalar(
                select(InventoryPosition).where(
                    InventoryPosition.environment == environment,
                    InventoryPosition.symbol == symbol,
                )
            )
            assert position is not None
            assert position.base_quantity == D("0.010000000000")
            assert position.quote_cost == D("1.000000000000")

        assert db.scalar(
            select(func.count(func.distinct(SpotFill.exchange_trade_id)))
        ) == 1

    engine.dispose()
