from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .exchange import BinanceSpot
from .grid_service import grid_order_type, materialize_levels, order_side_for_level
from .models import GridBot, GridLevel, OrderRequest, OrderSide, OrderStatus, SpotOrder
from .order_service import SubmitResult, create_intent, recover_or_submit, reconcile_order, record_fills

D = Decimal
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}


@dataclass(frozen=True)
class GridOrderPlan:
    level_index: int
    side: OrderSide
    price: D
    amount: D


def quote_per_grid(bot: GridBot) -> D:
    if bot.grids < 2:
        raise ValueError("grids must be at least 2")
    return bot.capital_quote / D(bot.grids - 1)


def plan_order(bot: GridBot, level: GridLevel, side: OrderSide) -> GridOrderPlan:
    if level.price <= 0:
        raise ValueError("grid level price must be positive")
    quote = quote_per_grid(bot)
    amount = quote / level.price
    return GridOrderPlan(level.level_index, side, level.price, amount)


def initial_order_plans(bot: GridBot, current_price: D) -> list[GridOrderPlan]:
    if current_price <= 0:
        raise ValueError("current price must be positive")
    levels = sorted(bot.levels, key=lambda item: item.level_index)
    return [
        plan_order(bot, level, order_side_for_level(current_price, level.price))
        for level in levels
        if level.price != current_price
    ]


def _linked_order(level: GridLevel, side: OrderSide, db: Session) -> SpotOrder | None:
    order_id = level.buy_order_id if side == OrderSide.BUY else level.sell_order_id
    return db.get(SpotOrder, order_id) if order_id is not None else None


def submit_grid_order(
    db: Session,
    adapter: BinanceSpot,
    bot: GridBot,
    plan: GridOrderPlan,
) -> SubmitResult:
    level = db.scalar(
        select(GridLevel).where(
            GridLevel.bot_id == bot.id,
            GridLevel.level_index == plan.level_index,
        )
    )
    if level is None:
        raise ValueError("grid level not found")
    existing = _linked_order(level, plan.side, db)
    if existing is not None:
        if existing.status in ACTIVE:
            return SubmitResult(existing, None)
        if existing.status == OrderStatus.FILLED.value:
            raise ValueError("grid level already has a filled order")
        raise ValueError(f"grid level has terminal order {existing.status}")

    request = OrderRequest(
        environment=__import__("spot_bot.models", fromlist=["Environment"]).Environment(bot.environment),
        symbol=bot.symbol,
        side=plan.side,
        order_type=grid_order_type(),
        amount=plan.amount,
        price=plan.price,
        post_only=True,
    )
    order = create_intent(db, request)
    if plan.side == OrderSide.BUY:
        level.buy_order_id = order.id
    else:
        level.sell_order_id = order.id
    db.commit()

    try:
        result = recover_or_submit(db, adapter, order, post_only=True)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def reconcile_grid_order(db: Session, adapter: BinanceSpot, bot: GridBot, order_id: int) -> SpotOrder:
    order = db.get(SpotOrder, order_id)
    if order is None:
        raise ValueError("spot order not found")
    if order.symbol != bot.symbol or order.environment != bot.environment:
        raise ValueError("order does not belong to grid environment/symbol")
    result = reconcile_order(db, adapter, order)
    record_fills(db, adapter, order)
    db.commit()
    return result


def opposite_plan(bot: GridBot, filled_order: SpotOrder) -> GridOrderPlan | None:
    levels = sorted(bot.levels, key=lambda item: item.level_index)
    linked = next((level for level in levels if level.buy_order_id == filled_order.id or level.sell_order_id == filled_order.id), None)
    if linked is None:
        return None
    opposite = OrderSide.SELL if filled_order.side == OrderSide.BUY.value else OrderSide.BUY
    target_index = linked.level_index + 1 if opposite == OrderSide.SELL else linked.level_index - 1
    if target_index < 0 or target_index >= bot.grids:
        return None
    target = levels[target_index]
    return plan_order(bot, target, opposite)


def advance_after_fill(
    db: Session,
    adapter: BinanceSpot,
    bot: GridBot,
    order_id: int,
) -> SubmitResult | None:
    order = reconcile_grid_order(db, adapter, bot, order_id)
    if order.status != OrderStatus.FILLED.value:
        return None
    plan = opposite_plan(bot, order)
    if plan is None:
        return None
    return submit_grid_order(db, adapter, bot, plan)
