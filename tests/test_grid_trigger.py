from decimal import Decimal as D

import ccxt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.grid_monitor import monitor_grid_once
from spot_bot.grid_service import materialize_levels
from spot_bot.models import Base, GridBot, GridLevel, GridStatus, OrderSide, SpotOrder


class TriggerAdapter:
    def __init__(self, price: str):
        self.price = price
        self.submissions = []
        self.orders = {}

    def ticker(self, symbol):
        return {"last": self.price}

    def validate_order(self, request):
        return None

    def create_order(self, request):
        oid = f"trigger-order-{len(self.submissions) + 1}"
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


def make_armed_grid():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=11,
        capital_quote=D("500"), max_open_orders=10, status=GridStatus.DRAFT.value,
        trigger_price=D("82000"),
    )
    db.add(bot)
    db.flush()
    for index in range(11):
        db.add(GridLevel(bot_id=bot.id, level_index=index, price=D(80000 + index * 1000)))
    db.flush()
    materialize_levels(db, bot)
    bot.status = GridStatus.ARMED.value
    db.commit()
    return engine, db, bot


def test_armed_grid_waits_until_price_is_at_or_below_trigger():
    _, db, bot = make_armed_grid()
    adapter = TriggerAdapter("82000.01")

    result = monitor_grid_once(db, bot, adapter)

    assert result["status"] == GridStatus.ARMED.value
    assert not result.get("triggered", False)
    assert adapter.submissions == []
    assert bot.status == GridStatus.ARMED.value


def test_downward_trigger_activates_once_when_price_touches_trigger():
    _, db, bot = make_armed_grid()
    adapter = TriggerAdapter("82000")

    result = monitor_grid_once(db, bot, adapter)

    assert result["triggered"] is True
    assert result["status"] == GridStatus.RUNNING.value
    assert len(adapter.submissions) == 2
    assert all(order.side == OrderSide.BUY for order in adapter.submissions)

    second = monitor_grid_once(db, bot, adapter)
    assert second["status"] == GridStatus.RUNNING.value
    assert len(adapter.submissions) == 2
    assert db.scalars(select(SpotOrder)).all()


def test_restart_recovers_armed_grid_when_price_has_already_crossed_trigger():
    engine, db, bot = make_armed_grid()
    bot_id = bot.id
    db.close()

    # Simulate process restart: reload only durable bot/level state in a new session.
    restarted_db = Session(engine)
    restarted_bot = restarted_db.get(GridBot, bot_id)
    adapter = TriggerAdapter("81000")

    result = monitor_grid_once(restarted_db, restarted_bot, adapter)

    assert result["triggered"] is True
    assert restarted_bot.status == GridStatus.RUNNING.value
    assert len(adapter.submissions) == 1
    assert adapter.submissions[0].price == D("80000.000000000000000000")
