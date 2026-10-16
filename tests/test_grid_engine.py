from decimal import Decimal as D

import ccxt
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from spot_bot.grid_engine import (
    advance_after_fill,
    initial_order_plans,
    pause_grid_execution,
    resume_grid_execution,
    stop_grid_execution,
    submit_grid_order,
)
from spot_bot.grid_service import pause_grid, start_grid
from spot_bot.models import Base, Environment, GridBot, GridLevel, InventoryApplication, OrderSide, OrderStatus, RealizedPNLEntry


class FakeAdapter:
    def __init__(self):
        self.submissions = []
        self.orders = {}
        self.trades = []
        self.cancel_race_mode = None

    def validate_order(self, request):
        assert request.environment == Environment.TESTNET

    def create_order(self, request):
        order_id = f"ex-{len(self.submissions) + 1}"
        self.submissions.append(request)
        raw = {"id": order_id, "status": "open", "filled": "0", "average": None, "fee": {"cost": "0", "currency": "USDT"}}
        self.orders[order_id] = raw
        return raw

    def cancel_order(self, order_id, symbol):
        raw = self.orders[order_id]
        if self.cancel_race_mode == "filled":
            raw["status"] = "closed"
            raw["filled"] = "1"
            raw["average"] = "80000"
            raise ccxt.OrderNotFound("already filled")
        if self.cancel_race_mode == "partial":
            raw["status"] = "canceled"
            raw["filled"] = "0.5"
            raise ccxt.InvalidOrder("cancel raced with partial fill")
        raw["status"] = "canceled"
        return raw

    def fetch_order(self, order_id, symbol):
        return self.orders[order_id]

    def fetch_order_by_client_id(self, symbol, client_order_id):
        raise ccxt.OrderNotFound("not found")

    def recent_trades(self, symbol, limit=100):
        return self.trades

    def ticker(self, symbol):
        return {"last": "85000"}


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_bot(db):
    bot = GridBot(environment="testnet", symbol="BTC/USDT", grid_type="arithmetic", lower_price=D("80000"), upper_price=D("90000"), grids=11, capital_quote=D("500"), max_open_orders=10)
    db.add(bot)
    db.flush()
    for index in range(11):
        db.add(GridLevel(bot_id=bot.id, level_index=index, price=D(80000 + index * 1000))
    db.flush()
    return bot


def start_test_grid(db, bot):
    start_grid(db, bot)
    db.commit()


def test_initial_plans_only_use_buy_levels_below_market():
    db = session()
    bot = make_bot(db)
    plans = initial_order_plans(bot, D("85000"))
    assert len(plans) == 5
    assert all(plan.side == OrderSide.BUY for plan in plans)
    assert [plan.level_index for plan in plans] == [0, 1, 2, 3, 4]


def test_submit_grid_order_commits_intent_before_exchange_submission():
    db = session(); bot = make_bot(db); start_test_grid(db, bot); adapter = FakeAdapter()
    result = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0])
    assert result.order.status == OrderStatus.OPEN.value
    assert result.order.exchange_order_id == "ex-1"
    assert len(adapter.submissions) == 1
    db.expire_all()
    assert db.get(type(result.order), result.order.id) is not None


def test_filled_buy_advances_to_next_sell_level():
    db = session(); bot = make_bot(db); start_test_grid(db, bot); adapter = FakeAdapter()
    first = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0])
    first_order = first.order
    adapter.orders[first_order.exchange_order_id].update(status="closed", filled=str(first_order.amount), average=str(first_order.price))
    result = advance_after_fill(db, adapter, bot, first_order.id)
    assert result is not None
    assert result.order.side == OrderSide.SELL.value
    assert result.order.price == D("81000")
    assert result.order.amount == first_order.amount
    assert len(adapter.submissions) == 2


def test_terminal_order_slot_can_be_reused():
    db = session(); bot = make_bot(db); start_test_grid(db, bot); adapter = FakeAdapter()
    plan = initial_order_plans(bot, D("85000"))[0]
    first = submit_grid_order(db, adapter, bot, plan); first.order.status = OrderStatus.CANCELED.value; db.commit()
    second = submit_grid_order(db, adapter, bot, plan)
    assert second.order.id != first.order.id
    assert len(adapter.submissions) == 2


def test_resume_does_not_recreate_filled_leg_and_reopens_canceled_leg():
    db = session(); bot = make_bot(db); bot.max_open_orders = 2; start_test_grid(db, bot); adapter = FakeAdapter()
    filled = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0]); filled_order = filled.order
    filled_order.status = OrderStatus.FILLED.value; filled_order.filled_amount = filled_order.amount; filled_order.average_price = filled_order.price; db.commit()
    canceled = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[1]); canceled.order.status = OrderStatus.CANCELED.value; db.commit()
    pause_grid(db, bot); db.commit(); results = resume_grid_execution(db, adapter, bot)
    assert len(results) == 2
    assert results[0].order.side == OrderSide.SELL.value
    assert results[0].order.price == D("81000")
    assert results[1].order.side == OrderSide.BUY.value
    assert results[1].order.price == D("81000")
    assert all(result.exchange is not None for result in results)


def test_resume_reopens_only_unfilled_remainder_of_partial_cancel():
    db = session(); bot = make_bot(db); bot.max_open_orders = 1; start_test_grid(db, bot); adapter = FakeAdapter()
    plan = initial_order_plans(bot, D("85000"))[0]; first = submit_grid_order(db, adapter, bot, plan); order = first.order
    order.status = OrderStatus.CANCELED.value; order.filled_amount = order.amount / D("2"); db.commit()
    pause_grid(db, bot); db.commit(); results = resume_grid_execution(db, adapter, bot)
    assert len(results) == 1
    assert results[0].order.side == OrderSide.BUY.value
    assert results[0].order.amount == order.amount - order.filled_amount
    assert results[0].exchange is not None


def test_grid_lifecycle_state_machine_pause_resume_stop():
    db = session(); bot = make_bot(db); bot.max_open_orders = 2; start_test_grid(db, bot); adapter = FakeAdapter()
    first = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0])
    second = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[1])
    paused_orders = pause_grid_execution(db, adapter, bot)
    assert bot.status == "paused" and {o.status for o in paused_orders} == {OrderStatus.CANCELED.value}
    resumed = resume_grid_execution(db, adapter, bot)
    assert bot.status == "running" and len(resumed) == 2 and all(r.exchange is not None for r in resumed)
    stopped_orders = stop_grid_execution(db, adapter, bot)
    assert bot.status == "stopped" and len(stopped_orders) == 2
    assert not [o for o in (first.order, second.order) if o.status in {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}]


def test_cancel_race_fill_wins_and_is_not_reopened():
    db = session(); bot = make_bot(db); bot.max_open_orders = 1; start_test_grid(db, bot); adapter = FakeAdapter()
    first = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0]); order = first.order
    adapter.cancel_race_mode = "filled"
    paused = pause_grid_execution(db, adapter, bot)
    assert bot.status == "paused"
    assert paused[0].status == OrderStatus.FILLED.value
    assert order.filled_amount == D("1")
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 0
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0
    resumed = resume_grid_execution(db, adapter, bot)
    assert len(resumed) == 1
    assert resumed[0].order.side == OrderSide.SELL.value
    assert all(result.exchange is not None for result in resumed)


def test_cancel_race_partial_fill_reopens_only_remainder():
    db = session(); bot = make_bot(db); bot.max_open_orders = 1; start_test_grid(db, bot); adapter = FakeAdapter()
    first = submit_grid_order(db, adapter, bot, initial_order_plans(bot, D("85000"))[0]); order = first.order
    adapter.cancel_race_mode = "partial"
    paused = pause_grid_execution(db, adapter, bot)
    assert bot.status == "paused"
    assert paused[0].status == OrderStatus.CANCELED.value
    assert order.filled_amount == D("0.5")
    resumed = resume_grid_execution(db, adapter, bot)
    assert len(resumed) == 1
    assert resumed[0].order.side == OrderSide.BUY.value
    assert resumed[0].order.amount == order.amount - D("0.5")
