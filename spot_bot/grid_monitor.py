from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import SessionLocal
from .exchange import BinanceSpot
from .grid_engine import execute_initial_grid, opposite_plan, reconcile_grid_order, submit_grid_order, _resume_terminal_orders
from .grid_service import grid_open_order_ids
from .models import Environment, GridBot, GridLevel, GridStatus, OrderSide, OrderStatus, SpotOrder

log = logging.getLogger(__name__)
D = Decimal
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}


def monitor_grid_once(db: Session, bot: GridBot, adapter: BinanceSpot) -> dict:
    """Reconcile resting grid orders and recover missed opposite legs after a fill.

    The exchange's resting LIMIT_MAKER orders are the price triggers. This loop
    observes confirmed exchange fills; it never fabricates fills from ticker data.
    """
    if bot.status not in {GridStatus.ARMED.value, GridStatus.RUNNING.value}:
        return {"bot_id": bot.id, "reconciled": 0, "advanced": 0, "status": bot.status}

    ticker = adapter.ticker(bot.symbol)
    price = D(str(ticker.get("last") or ticker.get("close") or "0"))
    if not price.is_finite() or price <= 0:
        raise RuntimeError(f"grid {bot.id}: ticker has no usable price")

    if bot.status == GridStatus.ARMED.value:
        trigger = D(str(bot.trigger_price)) if bot.trigger_price is not None else None
        if trigger is None:
            return {"bot_id": bot.id, "price": str(price), "reconciled": 0,
                    "advanced": 0, "status": bot.status, "trigger_error": "armed grid has no trigger price"}
        if price > trigger:
            return {"bot_id": bot.id, "price": str(price), "trigger_price": str(trigger),
                    "reconciled": 0, "advanced": 0, "status": bot.status}
        # Compare-and-set prevents duplicate activation when multiple monitors race.
        result = db.execute(
            update(GridBot)
            .where(GridBot.id == bot.id, GridBot.status == GridStatus.ARMED.value)
            .values(status=GridStatus.RUNNING.value)
        )
        if result.rowcount != 1:
            db.rollback()
            db.refresh(bot)
            return {"bot_id": bot.id, "price": str(price), "reconciled": 0,
                    "advanced": 0, "status": bot.status}
        db.commit()
        db.refresh(bot)
        submitted = execute_initial_grid(db, adapter, bot)
        return {"bot_id": bot.id, "price": str(price), "trigger_price": str(trigger),
                "reconciled": 0, "advanced": 0, "submitted": len(submitted),
                "status": bot.status, "triggered": True}

    # Recover a crash after activation committed but before first order intent.
    levels_before = db.scalars(select(GridLevel).where(GridLevel.bot_id == bot.id)).all()
    if levels_before and not any(level.buy_order_id or level.sell_order_id for level in levels_before):
        submitted = execute_initial_grid(db, adapter, bot)
        if submitted:
            return {"bot_id": bot.id, "price": str(price), "reconciled": 0,
                    "advanced": 0, "submitted": len(submitted), "status": bot.status,
                    "recovered_activation": True}

    reconciled = 0
    advanced = 0
    for order_id in list(grid_open_order_ids(db, bot)):
        order = db.get(SpotOrder, order_id)
        if order is None or order.status not in ACTIVE:
            continue
        order = reconcile_grid_order(db, adapter, bot, order_id)
        reconciled += 1
        if order.status == OrderStatus.FILLED.value:
            plan = opposite_plan(bot, order)
            if plan is None:
                continue
            target = db.scalar(select(GridLevel).where(
                GridLevel.bot_id == bot.id, GridLevel.level_index == plan.level_index
            ))
            if target is None:
                continue
            target_id = target.buy_order_id if plan.side == OrderSide.BUY else target.sell_order_id
            target_order = db.get(SpotOrder, target_id) if target_id is not None else None
            if target_order is not None:
                # Any linked order, including UNKNOWN, must be reconciled before
                # considering replacement. Never duplicate an ambiguous order.
                continue
            submit_grid_order(db, adapter, bot, plan)
            advanced += 1

    # Crash recovery: the previous pass may have committed a fill before it
    # managed to place the opposite leg. Filled links remain durable across restart.
    levels = sorted(db.scalars(select(GridLevel).where(GridLevel.bot_id == bot.id)).all(),
                    key=lambda row: row.level_index)
    for level in levels:
        for side in (OrderSide.BUY, OrderSide.SELL):
            order_id = level.buy_order_id if side == OrderSide.BUY else level.sell_order_id
            order = db.get(SpotOrder, order_id) if order_id is not None else None
            if order is None or order.status != OrderStatus.FILLED.value:
                continue
            plan = opposite_plan(bot, order)
            if plan is None:
                continue
            target = next((row for row in levels if row.level_index == plan.level_index), None)
            if target is None:
                continue
            target_id = target.buy_order_id if plan.side == OrderSide.BUY else target.sell_order_id
            target_order = db.get(SpotOrder, target_id) if target_id is not None else None
            if target_order is not None:
                # Terminal legs are handled by the remaining-quantity recovery below.
                continue
            submit_grid_order(db, adapter, bot, plan)
            advanced += 1

    levels = sorted(db.scalars(select(GridLevel).where(GridLevel.bot_id == bot.id)).all(),
                    key=lambda row: row.level_index)
    advanced += len(_resume_terminal_orders(db, adapter, bot, levels))
    return {"bot_id": bot.id, "price": str(price), "reconciled": reconciled,
            "advanced": advanced, "status": bot.status}


def monitor_running_grids_once(
    session_factory: Callable[[], Session] = SessionLocal,
    adapter_factory: Callable[[Environment], BinanceSpot] = BinanceSpot,
) -> None:
    db = session_factory()
    try:
        bots = db.scalars(select(GridBot).where(GridBot.status.in_([GridStatus.ARMED.value, GridStatus.RUNNING.value]))).all()
        adapters: dict[str, BinanceSpot] = {}
        for bot in bots:
            try:
                env = Environment(bot.environment)
                if env.value not in adapters:
                    adapters[env.value] = adapter_factory(env)
                monitor_grid_once(db, bot, adapters[env.value])
            except Exception:
                db.rollback()
                log.exception("Grid trigger monitor failed for bot %s", bot.id)
    finally:
        db.close()


async def grid_monitor_loop(stop_event: asyncio.Event, interval_seconds: float = 2.0) -> None:
    while not stop_event.is_set():
        await asyncio.to_thread(monitor_running_grids_once)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
