from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .exchange import BinanceSpot
from .grid_service import grid_open_order_ids, grid_order_type, pause_grid, start_grid, stop_grid
from .models import Environment, GridBot, GridLevel, OrderRequest, OrderSide, OrderStatus, SpotOrder
from .order_service import SubmitResult, cancel_order, create_intent, record_fills, recover_or_submit, reconcile_order

D = Decimal
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}
TERMINAL_FILLED = {OrderStatus.FILLED.value}
RESUMABLE = {OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value, OrderStatus.REJECTED.value}


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


def cancel_grid_orders(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SpotOrder]:
    """Cancel every locally-active order linked to a grid and reconcile the result."""
    orders: list[SpotOrder] = []
    for order_id in grid_open_order_ids(db, bot):
        order = db.get(SpotOrder, order_id)
        if order is None or order.status not in ACTIVE:
            continue
        try:
            cancel_order(db, adapter, order)
            record_fills(db, adapter, order)
            db.commit()
        except Exception:
            db.rollback()
            raise
        orders.append(order)
    return orders


def pause_grid_execution(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SpotOrder]:
    """Pause first, commit the state boundary, then cancel active exchange orders."""
    pause_grid(db, bot)
    db.commit()
    return cancel_grid_orders(db, adapter, bot)


def stop_grid_execution(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SpotOrder]:
    """Stop first, commit the terminal state boundary, then cancel active exchange orders."""
    stop_grid(db, bot)
    db.commit()
    return cancel_grid_orders(db, adapter, bot)


def _resumable_plan(bot: GridBot, order: SpotOrder, level: GridLevel) -> GridOrderPlan | None:
    """Reopen only the unfilled remainder of a canceled/expired/rejected leg."""
    if order.status not in RESUMABLE:
        return None
    filled = order.filled_amount or D("0")
    remaining = order.amount - filled
    if remaining <= 0:
        return None
    return plan_order(bot, level, OrderSide(order.side), remaining)


def resume_grid_execution(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SubmitResult]:
    """Resume the persisted grid lifecycle without rebuilding an existing grid."""
    start_grid(db, bot)
    db.commit()
    results: list[SubmitResult] = []

    levels = sorted(bot.levels, key=lambda item: item.level_index)
    linked_orders = [
        order_id
        for level in levels
        for order_id in (level.buy_order_id, level.sell_order_id)
        if order_id is not None
    ]

    # A newly materialized grid can be paused before its first order is placed.
    # In that one case resume is allowed to seed the initial grid. Once any leg
    # exists, resume is strictly a persisted-lifecycle repair operation.
    if not linked_orders:
        return execute_initial_grid(db, adapter, bot)

    # Filled legs advance first so the returned submission order is deterministic
    # and opposite legs are restored before canceled legs are reopened.
    for level in levels:
        for side in (OrderSide.BUY, OrderSide.SELL):
            existing = _linked_order(level, side, db)
            if existing is None or existing.status != OrderStatus.FILLED.value:
                continue
            plan = opposite_plan(bot, existing)
            if plan is None:
                continue
            target_level = next((item for item in levels if item.level_index == plan.level_index), None)
            if target_level is None:
                continue
            target_order = _linked_order(target_level, plan.side, db)
            if target_order is not None and target_order.status in ACTIVE:
                continue
            try:
                result = submit_grid_order(db, adapter, bot, plan)
                if result.exchange is not None:
                    results.append(result)
            except ValueError as exc:
                if "max_open_orders" in str(exc):
                    return results
                raise

    # Then restore only legs that existed before the pause and became terminal.
    for level in levels:
        for side in (OrderSide.BUY, OrderSide.SELL):
            existing = _linked_order(level, side, db)
            if existing is None or existing.status not in RESUMABLE:
                continue
            plan = _resumable_plan(bot, existing, level)
            if plan is None:
                continue
            try:
                result = submit_grid_order(db, adapter, bot, plan)
                if result.exchange is not None:
                    results.append(result)
            except ValueError as exc:
                if "max_open_orders" in str(exc):
                    return results
                raise

    return results


def opposite_plan(bot: GridBot, filled_order: SpotOrder) -> GridOrderPlan | None:
    levels = sorted(bot.levels, key=lambda item: item.level_index)
    linked = next((level for level in levels if level.buy_order_id == filled_order.id or level.sell_order_id == filled_order.id), None)
    if linked is None:
        return None
    opposite = OrderSide.SELL if filled_order.side == OrderSide.BUY.value else OrderSide.BUY
    target_index = linked.level_index + 1 if opposite == OrderSide.SELL else linked.level_index - 1
    if target_index < 0 or target_index >= bot.grids:
        return None
    target = next((level for level in levels if level.level_index == target_index), None)
    if target is None:
        return None
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
        level = db.scalar(
            select(GridLevel).where(
                GridLevel.bot_id == bot.id,
                GridLevel.level_index == plan.level_index,
            )
        )
        if level is None:
            continue
        existing = _linked_order(level, plan.side, db)
        if existing is not None and existing.status == OrderStatus.FILLED.value:
            continue
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
    if order.status != OrderStatus.FILLED.value or bot.status != "running":
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
