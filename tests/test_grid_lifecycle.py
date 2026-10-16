from decimal import Decimal as D

import ccxt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spot_bot.grid_engine import (
    GridOrderPlan,
    execute_initial_grid,
    opposite_plan,
    submit_grid_order,
)
from spot_bot.grid_service import (
    grid_open_order_ids,
    materialize_levels,
    next_grid_price,
    pause_grid,
    start_grid,
    stop_grid,
)
from spot_bot.models import Base, GridBot, GridStatus, OrderSide, SpotOrder


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_grid_start_materializes_levels_and_is_idempotent():
    db = db_session()
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=11,
        capital_quote=D("500"), max_open_orders=10,
    )
    db.add(bot)
    db.flush()

    levels = start_grid(db, bot)
    assert bot.status == GridStatus.RUNNING.value
    assert len(levels) == 11
    assert levels[0].price == D("80000")
    assert levels[-1].price == D("90000")

    with pytest.raises(ValueError):
        start_grid(db, bot)


def test_grid_pause_stop_lifecycle():
    db = db_session()
    bot = GridBot(
        environment="testnet", symbol="ETH/USDT", grid_type="arithmetic",
        lower_price=D("2000"), upper_price=D("2200"), grids=5,
        capital_quote=D("500"), max_open_orders=5,
    )
    db.add(bot)
    db.flush()
    start_grid(db, bot)
    pause_grid(db, bot)
    assert bot.status == GridStatus.PAUSED.value
    stop_grid(db, bot)
    assert bot.status == GridStatus.STOPPED.value


def test_next_grid_price_moves_one_level():
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=11,
        capital_quote=D("500"), max_open_orders=10,
    )
    assert next_grid_price(bot, 5, OrderSide.BUY) == D("84000")
    assert next_grid_price(bot, 5, OrderSide.SELL) == D("86000")
    assert next_grid_price(bot, 0, OrderSide.BUY) is None
    assert next_grid_price(bot, 10, OrderSide.SELL) is None


def test_grid_open_order_ids_only_returns_active_orders():
    db = db_session()
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=3,
        capital_quote=D("500"), max_open_orders=3,
    )
    db.add(bot)
    db.flush()
    levels = materialize_levels(db, bot)
    open_order = SpotOrder(environment="testnet", symbol="BTC/USDT", side="buy", order_type="limit_maker", status="open", amount=D("0.001"), price=D("80000"), client_order_id="spot-test-open-001")
    filled_order = SpotOrder(environment="testnet", symbol="BTC/USDT", side="buy", order_type="limit_maker", status="filled", amount=D("0.001"), price=D("80000"), client_order_id="spot-test-filled-01")
    db.add_all([open_order, filled_order])
    db.flush()
    levels[0].buy_order_id = open_order.id
    levels[1].buy_order_id = filled_order.id
    assert grid_open_order_ids(db, bot) == [open_order.id]


def test_opposite_plan_preserves_filled_base_amount_for_sell():
    db = db_session()
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=3,
        capital_quote=D("500"), max_open_orders=3,
    )
    db.add(bot)
    db.flush()
    levels = materialize_levels(db, bot)
    order = SpotOrder(
        environment="testnet", symbol="BTC/USDT", side="buy", order_type="limit_maker",
        status="filled", amount=D("0.003"), price=D("80000"), filled_amount=D("0.0029"),
        average_price=D("80010"), fee_quote=D("0.01"), client_order_id="spot-filled-base-01",
    )
    db.add(order)
    db.flush()
    levels[0].buy_order_id = order.id

    plan = opposite_plan(bot, order)
    assert plan is not None
    assert plan.side == OrderSide.SELL
    assert plan.level_index == 1
    assert plan.amount == D("0.0029")


def test_opposite_plan_buy_uses_sell_proceeds_after_fee():
    db = db_session()
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=3,
        capital_quote=D("500"), max_open_orders=3,
    )
    db.add(bot)
    db.flush()
    levels = materialize_levels(db, bot)
    order = SpotOrder(
        environment="testnet", symbol="BTC/USDT", side="sell", order_type="limit_maker",
        status="filled", amount=D("0.002"), price=D("90000"), filled_amount=D("0.002"),
        average_price=D("90000"), fee_quote=D("0.18"), client_order_id="spot-filled-quote-01",
    )
    db.add(order)
    db.flush()
    levels[2].sell_order_id = order.id

    plan = opposite_plan(bot, order)
    assert plan is not None
    assert plan.side == OrderSide.BUY
    assert plan.level_index == 1
    assert plan.amount == (D("0.002") * D("90000") - D("0.18")) / D("85000")


class FakeGridAdapter:
    def __init__(self):
        self.created = []

    def ticker(self, symbol):
        return {"last": "85000"}

    def fetch_order_by_client_id(self, symbol, client_order_id):
        raise ccxt.OrderNotFound("not found")

    def validate_order(self, request):
        return None

    def create_order(self, request):
        self.created.append(request)
        return {"id": str(len(self.created)), "status": "open", "filled": "0", "average": None}


def test_execute_initial_grid_respects_max_open_orders():
    db = db_session()
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=11,
        capital_quote=D("500"), max_open_orders=3,
    )
    db.add(bot)
    db.flush()
    materialize_levels(db, bot)
    start_grid(db, bot)
    db.commit()
    adapter = FakeGridAdapter()

    results = execute_initial_grid(db, adapter, bot)
    assert len(results) == 3
    assert len(adapter.created) == 3
    assert all(request.side == OrderSide.BUY for request in adapter.created)


def test_submit_grid_order_refuses_paused_bot():
    db = db_session()
    bot = GridBot(
        environment="testnet", symbol="BTC/USDT", grid_type="arithmetic",
        lower_price=D("80000"), upper_price=D("90000"), grids=3,
        capital_quote=D("500"), max_open_orders=3, status=GridStatus.PAUSED.value,
    )
    db.add(bot)
    db.flush()
    levels = materialize_levels(db, bot)
    adapter = FakeGridAdapter()
    with pytest.raises(ValueError, match="not running"):
        submit_grid_order(db, adapter, bot, GridOrderPlan(0, OrderSide.BUY, levels[0].price, D("0.002")))
