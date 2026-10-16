from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

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
