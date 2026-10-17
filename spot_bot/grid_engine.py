from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .exchange import BinanceSpot
from .grid_service import grid_open_order_ids, grid_order_type, pause_grid, start_grid, stop_grid
from .models import Environment, GridBot, GridLevel, OrderRequest, OrderSide, OrderStatus, OrderType, SpotOrder
from .order_service import SubmitResult, cancel_order, create_intent, record_fills, recover_or_submit, reconcile_order
from .profitability import calculate_grid_economics, enforce_min_net_profit

D = Decimal
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}
RESUMABLE = {OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value, OrderStatus.REJECTED.value}

@dataclass(frozen=True)
class GridOrderPlan:
    level_index: int
    side: OrderSide
    price: D
    amount: D

def quote_per_grid(bot: GridBot) -> D:
    if bot.grids < 2: raise ValueError("grids must be at least 2")
    return bot.capital_quote / D(bot.grids - 1)

def plan_order(bot: GridBot, level: GridLevel, side: OrderSide, amount: D | None = None) -> GridOrderPlan:
    if level.price <= 0: raise ValueError("grid level price must be positive")
    order_amount = amount if amount is not None else quote_per_grid(bot) / level.price
    if order_amount <= 0: raise ValueError("grid order amount must be positive")
    return GridOrderPlan(level.level_index, side, level.price, order_amount)

def initial_order_plans(bot: GridBot, current_price: D) -> list[GridOrderPlan]:
    if current_price <= 0: raise ValueError("current price must be positive")
    levels = sorted(bot.levels, key=lambda item: item.level_index)
    return [plan_order(bot, level, OrderSide.BUY) for level in levels if level.price < current_price][:bot.max_open_orders]

def _linked_order(level: GridLevel, side: OrderSide, db: Session) -> SpotOrder | None:
    order_id = level.buy_order_id if side == OrderSide.BUY else level.sell_order_id
    return db.get(SpotOrder, order_id) if order_id is not None else None

def _active_grid_order_count(db: Session, bot: GridBot) -> int:
    return len(grid_open_order_ids(db, bot))

def _normalize_grid_request(adapter: BinanceSpot, request: OrderRequest) -> OrderRequest:
    exchange = getattr(adapter, "exchange", None)
    if exchange is None: return request
    amount_fn = getattr(exchange, "amount_to_precision", None)
    price_fn = getattr(exchange, "price_to_precision", None)
    amount = D(str(amount_fn(request.symbol, request.amount))) if callable(amount_fn) else request.amount
    price = D(str(price_fn(request.symbol, request.price))) if request.price is not None and callable(price_fn) else request.price
    if amount <= 0: raise ValueError("grid order amount rounds to zero at Binance precision")
    if price is not None and price <= 0: raise ValueError("grid order price rounds to zero at Binance precision")
    market_fn = getattr(adapter, "market", None)
    market = market_fn(request.symbol) if callable(market_fn) else {}
    minimum = D(str((market.get("limits") or {}).get("amount", {}).get("min") or "0"))
    if minimum and amount < minimum: raise ValueError(f"grid order amount {amount} is below Binance minimum {minimum}")
    if request.order_type != OrderType.MARKET:
        cost = amount * (price or D("0"))
        minimum_cost = D(str((market.get("limits") or {}).get("cost", {}).get("min") or "0"))
        if minimum_cost and cost < minimum_cost: raise ValueError(f"grid order notional {cost} is below Binance minimum {minimum_cost}")
        for item in market.get("info", {}).get("filters", []):
            if item.get("filterType") in {"NOTIONAL", "MIN_NOTIONAL"} and item.get("minNotional") and cost < D(str(item["minNotional"])): raise ValueError(f"grid order notional {cost} violates Binance {item['filterType']} filter")
    return OrderRequest(request.environment, request.symbol, request.side, request.order_type, amount, price, request.client_order_id, request.post_only)

def _guard_profitability(adapter: BinanceSpot, bot: GridBot, plan: GridOrderPlan, request: OrderRequest) -> None:
    levels = sorted(bot.levels, key=lambda item: item.level_index)
    target_index = plan.level_index + 1 if plan.side == OrderSide.BUY else plan.level_index - 1
    if target_index < 0:
        raise ValueError("grid order has no adjacent level; profitability cannot be verified")
    target = next((level for level in levels if level.level_index == target_index), None)
    if target is None:
        raise ValueError("adjacent grid level is missing; profitability cannot be verified")
    buy_price = request.price if plan.side == OrderSide.BUY else target.price
    sell_price = target.price if plan.side == OrderSide.BUY else request.price
    if buy_price is None or sell_price is None: raise ValueError("limit grid economics require both prices")
    # Normalize the complete adjacent cycle, not just the order being submitted.
    opposite_side = OrderSide.SELL if plan.side == OrderSide.BUY else OrderSide.BUY
    opposite_request = OrderRequest(request.environment, request.symbol, opposite_side, request.order_type, request.amount, target.price, post_only=True)
    buy_req = request if plan.side == OrderSide.BUY else opposite_request
    sell_req = opposite_request if plan.side == OrderSide.BUY else request
    buy_req = _normalize_grid_request(adapter, buy_req)
    sell_req = _normalize_grid_request(adapter, sell_req)
    buy_price = buy_req.price
    sell_price = sell_req.price
    if buy_price is None or sell_price is None:
        raise ValueError("normalized cycle requires both limit prices")
    qty = min(buy_req.amount, sell_req.amount)
    economics = calculate_grid_economics(buy_price, sell_price, qty, get_settings().grid_maker_fee_pct, get_settings().grid_slippage_pct)
    enforce_min_net_profit(economics, get_settings().grid_min_net_profit_pct)

def submit_grid_order(db: Session, adapter: BinanceSpot, bot: GridBot, plan: GridOrderPlan) -> SubmitResult:
    if bot.status != "running": raise ValueError(f"grid {bot.id} is not running")
    level = db.scalar(select(GridLevel).where(GridLevel.bot_id == bot.id, GridLevel.level_index == plan.level_index))
    if level is None: raise ValueError("grid level not found")
    existing = _linked_order(level, plan.side, db)
    if existing is not None and existing.status in ACTIVE: return SubmitResult(existing, None)
    if existing is not None:
        if plan.side == OrderSide.BUY: level.buy_order_id = None
        else: level.sell_order_id = None
        db.flush()
    if _active_grid_order_count(db, bot) >= bot.max_open_orders: raise ValueError("grid max_open_orders limit reached")
    request = OrderRequest(Environment(bot.environment), bot.symbol, plan.side, grid_order_type(), plan.amount, plan.price, post_only=True)
    request = _normalize_grid_request(adapter, request)
    _guard_profitability(adapter, bot, plan, request)
    order = create_intent(db, request)
    if plan.side == OrderSide.BUY: level.buy_order_id = order.id
    else: level.sell_order_id = order.id
    db.commit()
    try:
        result = recover_or_submit(db, adapter, order, post_only=True)
        db.commit()
        return result
    except Exception:
        db.rollback(); raise

def reconcile_grid_order(db: Session, adapter: BinanceSpot, bot: GridBot, order_id: int) -> SpotOrder:
    order = db.get(SpotOrder, order_id)
    if order is None: raise ValueError("spot order not found")
    if order.symbol != bot.symbol or order.environment != bot.environment: raise ValueError("order does not belong to grid environment/symbol")
    result = reconcile_order(db, adapter, order); record_fills(db, adapter, order); db.commit(); return result

def cancel_grid_orders(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SpotOrder]:
    orders=[]
    for order_id in grid_open_order_ids(db, bot):
        order=db.get(SpotOrder, order_id)
        if order is None or order.status not in ACTIVE: continue
        try: cancel_order(db, adapter, order); record_fills(db, adapter, order); db.commit()
        except Exception: db.rollback(); raise
        orders.append(order)
    return orders

def pause_grid_execution(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SpotOrder]:
    pause_grid(db, bot); db.commit(); return cancel_grid_orders(db, adapter, bot)

def stop_grid_execution(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SpotOrder]:
    stop_grid(db, bot); db.commit(); return cancel_grid_orders(db, adapter, bot)

def _resumable_plan(bot: GridBot, order: SpotOrder, level: GridLevel) -> GridOrderPlan | None:
    if order.status not in RESUMABLE: return None
    remaining=order.amount-max(D("0"), order.filled_amount or D("0"))
    return plan_order(bot, level, OrderSide(order.side), remaining) if remaining > 0 else None

def _resume_terminal_orders(db: Session, adapter: BinanceSpot, bot: GridBot, levels: list[GridLevel]) -> list[SubmitResult]:
    results=[]
    for level in levels:
        for side in (OrderSide.BUY, OrderSide.SELL):
            existing=_linked_order(level, side, db)
            if existing is None or existing.status not in RESUMABLE: continue
            plan=_resumable_plan(bot, existing, level)
            if plan is None: continue
            try: results.append(submit_grid_order(db,adapter,bot,plan))
            except ValueError as exc:
                if "max_open_orders" in str(exc): return results
                raise
    return results

def resume_grid_execution(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SubmitResult]:
    levels=sorted(start_grid(db, bot), key=lambda item:item.level_index); db.commit(); results=[]
    if not any(level.buy_order_id or level.sell_order_id for level in levels): return execute_initial_grid(db,adapter,bot)
    for level in levels:
        for side in (OrderSide.BUY,OrderSide.SELL):
            existing=_linked_order(level,side,db)
            if existing is None or existing.status != OrderStatus.FILLED.value: continue
            plan=opposite_plan(bot,existing)
            if plan is None: continue
            target=next((item for item in levels if item.level_index==plan.level_index),None)
            if target is None: continue
            target_order=_linked_order(target,plan.side,db)
            if target_order is not None and target_order.status in ACTIVE: continue
            try: results.append(submit_grid_order(db,adapter,bot,plan))
            except ValueError as exc:
                if "max_open_orders" in str(exc): return results
                raise
    results.extend(_resume_terminal_orders(db,adapter,bot,levels)); return results

def opposite_plan(bot: GridBot, filled_order: SpotOrder) -> GridOrderPlan | None:
    levels=sorted(bot.levels,key=lambda item:item.level_index)
    linked=next((level for level in levels if level.buy_order_id==filled_order.id or level.sell_order_id==filled_order.id),None)
    if linked is None: return None
    opposite=OrderSide.SELL if filled_order.side==OrderSide.BUY.value else OrderSide.BUY
    target_index=linked.level_index+1 if opposite==OrderSide.SELL else linked.level_index-1
    if target_index<0 or target_index>=bot.grids: return None
    target=next((level for level in levels if level.level_index==target_index),None)
    if target is None: return None
    if opposite==OrderSide.SELL: amount=filled_order.filled_amount
    else:
        gross_quote=filled_order.filled_amount*(filled_order.average_price or filled_order.price or D("0")); amount=(gross_quote-filled_order.fee_quote)/target.price
    return plan_order(bot,target,opposite,amount)

def execute_initial_grid(db: Session, adapter: BinanceSpot, bot: GridBot) -> list[SubmitResult]:
    if bot.status!="running": raise ValueError(f"grid {bot.id} is not running")
    ticker=adapter.ticker(bot.symbol); current=D(str(ticker.get("last") or ticker.get("close") or "0"))
    if current<=0: raise ValueError("exchange ticker has no usable price")
    results=[]
    for plan in initial_order_plans(bot,current):
        level=db.scalar(select(GridLevel).where(GridLevel.bot_id==bot.id,GridLevel.level_index==plan.level_index))
        if level is None: continue
        existing=_linked_order(level,plan.side,db)
        if existing is not None and existing.status==OrderStatus.FILLED.value: continue
        try: results.append(submit_grid_order(db,adapter,bot,plan))
        except ValueError as exc:
            if "max_open_orders" in str(exc): break
            raise
    return results

def advance_after_fill(db: Session, adapter: BinanceSpot, bot: GridBot, order_id: int) -> SubmitResult | None:
    order=reconcile_grid_order(db,adapter,bot,order_id)
    if order.status!=OrderStatus.FILLED.value or bot.status!="running": return None
    plan=opposite_plan(bot,order)
    return submit_grid_order(db,adapter,bot,plan) if plan is not None else None

def execute_grid_order(db: Session, adapter: BinanceSpot, bot: GridBot, order_id: int) -> SubmitResult | None:
    return advance_after_fill(db,adapter,bot,order_id)
