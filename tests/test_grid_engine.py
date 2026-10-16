from decimal import Decimal as D

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spot_bot.grid_engine import (
    advance_after_fill,
    initial_order_plans,
    opposite_plan,
    submit_grid_order,
)
from spot_bot.models import Base, Environment, GridBot, GridLevel, OrderSide, OrderStatus


class FakeAdapter:
    def __init__(self):
        self.submissions = []
        self.orders = {}
        self.trades = []

    def validate_order(self, request):
        assert request.environment == Environment.TESTNET

    def create_order(self, request):
        order_id = f"ex-{len(self.submissions) + 1}"
        self.submissions.append(request)
        raw = {
            "id": order_id,
            "status": "open",
            "filled": "0",
            "average": None,
            "fee": {"cost": "0", "currency": "USDT"},
        }
        self.orders[order_id] = raw
        return raw

    def fetch_order(self, order_id, symbol):
        return self.orders[order_id]

    def fetch_order_by_client_id(self, symbol, client_order_id):
        raise __import__("ccxt").OrderNotFound("not found")

    def recent_trades(self, symbol, limit=100):
        return self.trades


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_bot(db):
    bot = GridBot(
        environment="testnet",
        symbol="BTC/USDT",
        grid_type="arithmetic",
        lower_price=D("80000"),
        upper_price=D("90000"),
        grids=11,
        capital_quote=D("500"),
        max_open_orders=10,
    )
    db.add(bot)
    db.flush()
    for index in range(11):
        db.add(GridLevel(bot_id=bot.id, level_index=index, price=D(80000 + index * 1000)))
    db.flush()
    return bot


def test_initial_plans_only_use_buy_levels_below_market():
    db = session()
    bot = make_bot(db)
    plans = initial_order_plans(bot, D("85000"))
    assert len(plans) == 5
    assert all(plan.side == OrderSide.BUY for plan in plans)
    assert [plan.level_index for plan in plans] == [0, 1, 2, 3, 4]


def test_submit_grid_order_commits_intent_before_exchange_submission():
    db = session()
    bot = make_bot(db)
    adapter = FakeAdapter()
    plan = initial_order_plans(bot, D("85000"))[0]

    result = submit_grid_order(db, adapter, bot, plan)

    assert result.order.status == OrderStatus.OPEN.value
    assert result.order.exchange_order_id == "ex-1"
    assert len(adapter.submissions) == 1
    db.expire_all()
    saved = db.get(type(result.order), result.order.id)
    assert saved is not None
    assert saved.client_order_id


def test_filled_buy_advances_to_next_sell_level():
    db = session()
    bot = make_bot(db)
    adapter = FakeAdapter()
    first = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0])
    first_order = first.order
    adapter.orders[first_order.exchange_order_id]["status"] = "closed"
    adapter.orders[first_order.exchange_order_id]["filled"] = str(first_order.amount)
    adapter.orders[first_order.exchange_order_id]["average"] = str(first_order.price)

    next_plan = opposite_plan(bot, first_order)
    assert next_plan is not None
    assert next_plan.side == OrderSide.SELL
    assert next_plan.level_index == 1

    result = advance_after_fill(db, adapter, bot, first_order.id)
    assert result is not None
    assert result.order.side == OrderSide.SELL.value
    assert result.order.price == D("81000")
    assert len(adapter.submissions) == 2


def test_terminal_order_slot_can_be_reused():
    db = session()
    bot = make_bot(db)
    adapter = FakeAdapter()
    plan = initial_order_plans(bot, D("85000"))[0]
    first = submit_grid_order(db, adapter, bot, plan)
    first.order.status = OrderStatus.CANCELED.value
    db.commit()

    second = submit_grid_order(db, adapter, bot, plan)
    assert second.order.id != first.order.id
    assert len(adapter.submissions) == 2
