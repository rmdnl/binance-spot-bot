from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .exchange import BinanceSpot
from .grid_service import grid_order_type
from .models import Environment, GridBot, GridLevel, OrderRequest, OrderSide, OrderStatus, SpotOrder
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


def plan_order(bot: GridBot, level: GridLevel, side: OrderSide, amount: D | None = None) -> GridOrderPlan:
    if level.price <= 0:
        raise ValueError("grid level price must be positive")
    order_amount = amount if amount is not None else quote_per_grid(bot) / level.price
    if order_amount <= 0:
        raise ValueError("grid order amount must be positive")
    return GridOrderPlan(level.level_index, side, level.price, order_amount)


def initial_order_plans(bot: GridBot, current_price: D) -> list[GridOrderPlan]:
    if current_price <= 0:
        raise ValueError("current price must be positive")
    levels = sorted(bot.levels, key=lambda item: item.level_index)
    plans = [plan_order(bot, level, OrderSide.BUY) for level in levels if level.price < current_price]
    return plans[: bot.max_open_orders]


def _linked_order(level: GridLevel, side: OrderSide, db: Session) -> SpotOrder | None:
    order_id = level.buy_order_id if side == OrderSide.BUY else level.sell_order_id
    return db.get(SpotOrder, order_id) if order_id is not None else None


def _active_grid_order_count(db: Session, bot: GridBot) -> int:
    return len(grid_open_order_ids(db, bot))


def submit_grid_order(
    db: Session,
    adapter: BinanceSpot,
    bot: GridBot,
    plan: GridOrderPlan,
) -> SubmitResult:
    if bot.status != "running":
        raise ValueError(f"grid {bot.id} is not running")
    level = db.scalar(
        select(GridLevel).where(
            GridLevel.bot_id == bot.id,
            GridLevel.level_index == plan.level_index,
        )
    )
    if level is None:
        raise ValueError("grid level not found")
    existing = _linked_order(level, plan.side, db)
    if existing is not None and existing.status in ACTIVE:
        return SubmitResult(existing, None)
    if _active_grid_order_count(db, bot) >= bot.max_open_orders:
        raise ValueError("grid max_open_orders limit reached")
    if existing is not None:
        if plan.side == OrderSide.BUY:
            level.buy_order_id = None
        else:
            level.sell_order_id = None
        db.flush()

    request = OrderRequest(
        environment=Environment(bot.environment),
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
    if opposite == OrderSide.SELL:
        amount = filled_order.filled_amount
    else:
        gross_quote = filled_order.filled_amount * (filled_order.average_price or filled_order.price or D("0"))
        amount = (gross_quote - filled_order.fee_quote) / target.price
    return plan_order(bot, target, opposite, amount)


def execute_initial_grid(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SubmitResult]:
    if bot.status != "running":
        raise ValueError(f"grid {bot.id} is not running")
    ticker = adapter.ticker(bot.symbol)
    current = D(str(ticker.get("last") or ticker.get("close") or "0"))
    if current <= 0:
        raise ValueError("exchange ticker has no usable price")
    results: list[SubmitResult] = []
    for plan in initial_order_plans(bot, current):
        try:
            results.append(submit_grid_order(db, adapter, bot, plan))
        except ValueError as exc:
            if "max_open_orders" in str(exc):
                break
            raise
    return results


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


def execute_grid_order(
    db: Session,
    adapter: BinanceSpot,
    bot: GridBot,
    order_id: int,
) -> SubmitResult | None:
    return advance_after_fill(db, adapter, bot, order_id)
