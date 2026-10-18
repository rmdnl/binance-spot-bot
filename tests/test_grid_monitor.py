from decimal import Decimal as D

import ccxt

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.grid_engine import submit_grid_order
from spot_bot.grid_monitor import monitor_grid_once
from spot_bot.grid_service import start_grid
from spot_bot.models import Base, GridBot, GridLevel, OrderSide, OrderStatus, SpotOrder


class MonitorAdapter:
    def __init__(self):
        self.submissions = []
        self.orders = {}

    def ticker(self, symbol):
        return {"last": "85000"}

    def validate_order(self, request):
        return None

    def create_order(self, request):
        oid = f"exchange-{len(self.submissions) + 1}"
        self.submissions.append(request)
        raw = {"id": oid, "status": "open", "amount": str(request.amount),
               "filled": "0", "average": None, "fee": {"cost": "0", "currency": "USDT"}}
        self.orders[oid] = raw
        return raw

    def fetch_order(self, order_id, symbol):
        return self.orders[order_id]

    def fetch_order_by_client_id(self, symbol, client_order_id):
        raise ccxt.OrderNotFound(client_order_id)

    def recent_trades(self, symbol, limit=100):
        return []


def setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    bot = GridBot(environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
                  lower_price=D("80000"), upper_price=D("90000"), grids=11,
                  capital_quote=D("500"), max_open_orders=10, status="draft")
    db.add(bot)
    db.flush()
    for index in range(11):
        db.add(GridLevel(bot_id=bot.id, level_index=index, price=D(80000 + index * 1000)))
    db.flush()
    start_grid(db, bot)
    db.commit()
    return db, bot


def test_monitor_recovers_opposite_leg_after_restart_window():
    db, bot = setup()
    adapter = MonitorAdapter()
    first = submit_grid_order(
        db, adapter, bot,
        __import__("spot_bot.grid_engine", fromlist=["initial_order_plans"]).initial_order_plans(bot, D("85000"))[0],
    ).order
    # Simulate a crash after the filled state was committed but before its sell leg.
    first.status = OrderStatus.FILLED.value
    first.filled_amount = first.amount
    first.average_price = first.price
    db.commit()

    result = monitor_grid_once(db, bot, adapter)
    assert result["status"] == "running"
    assert result["advanced"] == 1
    assert len(adapter.submissions) == 2
    assert adapter.submissions[-1].side == OrderSide.SELL

    monitor_grid_once(db, bot, adapter)
    assert len(adapter.submissions) == 2
    assert db.scalar(select(func.count()).select_from(SpotOrder)) == 2


def test_monitor_reconciles_exchange_fill_and_places_opposite_leg():
    db, bot = setup()
    adapter = MonitorAdapter()
    from spot_bot.grid_engine import initial_order_plans
    first = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0]).order
    adapter.orders[first.exchange_order_id].update(
        status="closed", filled=str(first.amount), average=str(first.price)
    )

    result = monitor_grid_once(db, bot, adapter)

    assert result["reconciled"] == 1
    assert len(adapter.submissions) == 2
    sell = db.scalars(select(SpotOrder).where(SpotOrder.side == "sell")).first()
    assert sell is not None
    assert sell.status == OrderStatus.OPEN.value
