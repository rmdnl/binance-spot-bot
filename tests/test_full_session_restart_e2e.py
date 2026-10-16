from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import Base, Environment, InventoryApplication, InventoryPosition, RealizedPNLEntry, SpotFill, SpotOrder
from spot_bot.order_service import reconcile_order


class SessionAdapter:
    def __init__(self, orders, trades):
        self.orders = orders
        self.trades = trades

    def fetch_order(self, exchange_order_id, symbol):
        return self.orders[str(exchange_order_id)]

    def recent_trades(self, symbol, limit=100):
        return [trade for trade in self.trades if trade["symbol"] == symbol]


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_full_session_restart_reconciles_and_resumes_without_duplicate_accounting():
    engine = make_db()
    with Session(engine) as db:
        position = InventoryPosition(environment=Environment.TESTNET.value, symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT", base_quantity=D("0"), quote_cost=D("0"))
        buy = SpotOrder(environment=Environment.TESTNET.value, symbol="BTC/USDT", side="buy", order_type="limit", status="open", amount=D("0.02"), price=D("80000"), client_order_id="session-buy", exchange_order_id="ex-buy")
        sell = SpotOrder(environment=Environment.TESTNET.value, symbol="BTC/USDT", side="sell", order_type="limit", status="open", amount=D("0.01"), price=D("81000"), client_order_id="session-sell", exchange_order_id="ex-sell")
        db.add_all([position, buy, sell])
        db.commit()

    adapter = SessionAdapter(
        {
            "ex-buy": {"id": "ex-buy", "status": "closed", "filled": "0.02", "average": "80000", "fee": {"cost": "0.16", "currency": "USDT"}},
            "ex-sell": {"id": "ex-sell", "status": "closed", "filled": "0.01", "average": "81000", "fee": {"cost": "0.081", "currency": "USDT"}},
        },
        [
            {"id": "trade-buy", "order": "ex-buy", "symbol": "BTC/USDT", "amount": "0.02", "price": "80000", "cost": "1600", "fee": {"cost": "0.16", "currency": "USDT"}},
            {"id": "trade-sell", "order": "ex-sell", "symbol": "BTC/USDT", "amount": "0.01", "price": "81000", "cost": "810", "fee": {"cost": "0.081", "currency": "USDT"}},
        ],
    )

    try:
        with Session(engine) as db:
            for order in db.scalars(select(SpotOrder).order_by(SpotOrder.id)).all():
                reconcile_order(db, adapter, order)
            raise RuntimeError("simulated bot crash before session commit")
    except RuntimeError:
        pass

    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 0
        position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
        assert position.base_quantity == D("0")
        assert position.quote_cost == D("0")

        for order in db.scalars(select(SpotOrder).order_by(SpotOrder.id)).all():
            reconcile_order(db, adapter, order)
        for fill in db.scalars(select(SpotFill).order_by(SpotFill.id)).all():
            apply_fill(db, fill)
        db.commit()

    with Session(engine) as db:
        for order in db.scalars(select(SpotOrder).order_by(SpotOrder.id)).all():
            reconcile_order(db, adapter, order)
        for fill in db.scalars(select(SpotFill).order_by(SpotFill.id)).all():
            apply_fill(db, fill)
        db.commit()

        position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
        assert position.base_quantity == D("0.01")
        assert position.quote_cost == D("800.079")
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 2
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 2
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1
        pnl = db.scalar(select(RealizedPNLEntry))
        assert pnl.realized_pnl == D("9.839")
