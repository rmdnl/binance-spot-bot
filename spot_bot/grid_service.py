from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .grid import GridPreview, build_grid
from .models import GridBot, GridConfig, GridLevel, GridStatus, OrderSide, OrderType, SpotOrder

D = Decimal
ACTIVE_ORDER_STATUSES = {"new", "open", "partially_filled"}


def validate_grid_config(config: GridConfig) -> GridPreview:
    if not config.symbol or "/" not in config.symbol:
        raise ValueError("symbol must use BASE/QUOTE format")
    if config.lower_price <= 0 or config.upper_price <= config.lower_price:
        raise ValueError("invalid grid range")
    if config.capital <= 0:
        raise ValueError("capital must be positive")
    if config.max_open_orders < 1:
        raise ValueError("max_open_orders must be positive")
    preview = build_grid(config.lower_price, config.upper_price, config.grids, config.grid_type)
    if config.stop_loss_enabled and config.stop_loss_price is None:
        raise ValueError("stop loss price is required when stop loss is enabled")
    if config.stop_loss_price is not None and (config.stop_loss_price <= 0 or config.stop_loss_price >= preview.lower_price):
        raise ValueError("stop loss must be below lower grid price")
    return preview


def initial_grid_orders(config: GridConfig) -> GridPreview:
    return validate_grid_config(config)


def materialize_levels(db: Session, bot: GridBot) -> list[GridLevel]:
    if bot.status not in {GridStatus.DRAFT.value, GridStatus.PAUSED.value}:
        raise ValueError(f"grid {bot.id} cannot be materialized from {bot.status}")
    config = GridConfig(bot.symbol, bot.lower_price, bot.upper_price, bot.grids, bot.capital_quote, bot.grid_type, bot.max_open_orders, bot.stop_loss_price, bot.stop_loss_enabled)
    preview = validate_grid_config(config)
    existing = db.scalars(select(GridLevel).where(GridLevel.bot_id == bot.id)).all()
    by_index = {level.level_index: level for level in existing}
    levels: list[GridLevel] = []
    for level in preview.levels:
        row = by_index.get(level.index)
        if row is None:
            row = GridLevel(bot_id=bot.id, level_index=level.index, price=level.price)
            db.add(row)
        else:
            row.price = level.price
        levels.append(row)
    db.flush()
    return levels


def start_grid(db: Session, bot: GridBot) -> list[GridLevel]:
    if bot.status not in {GridStatus.DRAFT.value, GridStatus.PAUSED.value}:
        raise ValueError(f"grid {bot.id} cannot start from {bot.status}")
    levels = materialize_levels(db, bot)
    bot.status = GridStatus.RUNNING.value
    db.flush()
    return levels


def pause_grid(db: Session, bot: GridBot) -> GridBot:
    if bot.status != GridStatus.RUNNING.value:
        raise ValueError(f"grid {bot.id} cannot pause from {bot.status}")
    bot.status = GridStatus.PAUSED.value
    db.flush()
    return bot


def stop_grid(db: Session, bot: GridBot) -> GridBot:
    if bot.status not in {GridStatus.ARMED.value, GridStatus.RUNNING.value, GridStatus.PAUSED.value, GridStatus.ERROR.value}:
        raise ValueError(f"grid {bot.id} cannot stop from {bot.status}")
    bot.status = GridStatus.STOPPED.value
    db.flush()
    return bot


def grid_open_order_ids(db: Session, bot: GridBot) -> list[int]:
    levels = db.scalars(select(GridLevel).where(GridLevel.bot_id == bot.id)).all()
    ids = [order_id for level in levels for order_id in (level.buy_order_id, level.sell_order_id) if order_id is not None]
    if not ids:
        return []
    active = db.scalars(select(SpotOrder).where(SpotOrder.id.in_(ids))).all()
    return [order.id for order in active if order.status in ACTIVE_ORDER_STATUSES]


def next_grid_price(bot: GridBot, level_index: int, side: OrderSide) -> D | None:
    if side == OrderSide.BUY and level_index <= 0:
        return None
    if side == OrderSide.SELL and level_index >= bot.grids - 1:
        return None
    preview = build_grid(bot.lower_price, bot.upper_price, bot.grids, bot.grid_type)
    target = level_index - 1 if side == OrderSide.BUY else level_index + 1
    return preview.levels[target].price


def order_side_for_level(current_price: D, level_price: D) -> OrderSide:
    return OrderSide.BUY if level_price < current_price else OrderSide.SELL


def grid_order_type() -> OrderType:
    return OrderType.LIMIT_MAKER
