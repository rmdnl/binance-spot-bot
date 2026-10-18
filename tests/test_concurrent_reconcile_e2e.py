from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from threading import Barrier

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import Base, Environment, InventoryApplication, InventoryPosition, RealizedPNLEntry, SpotFill, SpotOrder
from spot_bot.order_service import reconcile_order


class ConcurrentAdapter:
    def __init__(self, barrier):
        self.barrier = barrier
        self.trades = [{"id": "trade-concurrent-1", "order": "exchange-1", "amount": "0.01", "price": "80000", "cost": "800", "fee": {"cost": "0.08", "currency": "USDT"}}]

    def fetch_order(self, exchange_order_id, symbol):
        self.barrier.wait()
        return {"id": "exchange-1", "status": "closed", "filled": "0.01", "average": "80000", "fee": {"cost": "0.08", "currency": "USDT"}}

    def recent_trades(self, symbol, limit=100):
        self.barrier.wait()
        return self.trades


def make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-reconcile.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    return engine


def seed(engine):
    with Session(engine) as db:
        position = InventoryPosition(environment=Environment.TESTNET.value, symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT", base_quantity=D("0"), quote_cost=D("0"))
        order = SpotOrder(environment=Environment.TESTNET.value, symbol="BTC/USDT", side="buy", order_type="limit", status="open", amount=D("0.01"), price=D("80000"), client_order_id="concurrent-reconcile-1", exchange_order_id="exchange-1")
        db.add_all([position, order])
        db.commit()
        return order.id


def test_concurrent_reconcile_same_order_does_not_duplicate_fill_or_accounting(tmp_path):
    engine = make_db(tmp_path)
    order_id = seed(engine)
    exchange_barrier = Barrier(2)
    apply_barrier = Barrier(2)

    def worker():
        with Session(engine) as db:
            order = db.get(SpotOrder, order_id)
            reconcile_order(db, ConcurrentAdapter(exchange_barrier), order)
            db.commit()
            fill = db.scalar(select(SpotFill).where(SpotFill.order_id == order_id))
            if fill:
                # Both workers have loaded the same unapplied fill before either
                # can claim it, forcing the inventory idempotency race.
                apply_barrier.wait()
                apply_fill(db, fill)
                db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))

    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 1
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0
        position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
        assert position.base_quantity == D("0.01")
        assert position.quote_cost == D("800.08")
