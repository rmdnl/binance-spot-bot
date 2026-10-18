from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .exchange import BinanceSpot
from .grid_engine import execute_initial_grid, pause_grid_execution, resume_grid_execution, stop_grid_execution
from .grid_service import grid_open_order_ids, initial_grid_orders, materialize_levels, start_grid
from .models import Environment, GridBot, GridConfig, GridLevel, GridStatus

router = APIRouter(prefix="/api/grid", tags=["grid"])
D = Decimal


def guard(request: Request, csrf_token: str) -> None:
    from .main import auth, csrf_valid

    if not auth(request):
        raise HTTPException(401, "Authentication required")
    if not csrf_valid(csrf_token, request):
        raise HTTPException(403, "Invalid CSRF token")


def get_bot(db: Session, bot_id: int) -> GridBot:
    bot = db.get(GridBot, bot_id)
    if bot is None:
        from fastapi import HTTPException
        raise HTTPException(404, "Grid bot not found")
    return bot


def adapter_for(bot: GridBot) -> BinanceSpot:
    return BinanceSpot(Environment(bot.environment))


@router.post("/bots")
def create_grid(
    request: Request, environment: str = Form(...), symbol: str = Form(...), lower_price: str = Form(...),
    upper_price: str = Form(...), grids: int = Form(...), capital_quote: str = Form(...),
    grid_type: str = Form("arithmetic"), max_open_orders: int = Form(20), trigger_price: str = Form(""), csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        guard(request, csrf_token)
        env = Environment(environment.lower())
        config = GridConfig(symbol.upper(), D(lower_price), D(upper_price), grids, D(capital_quote), grid_type.lower(), max_open_orders)
        preview = initial_grid_orders(config)
        trigger = D(trigger_price) if trigger_price.strip() else None
        if trigger is not None and (not trigger.is_finite() or trigger <= 0):
            raise ValueError("trigger price must be a finite positive number")
        bot = GridBot(
            environment=env.value, symbol=config.symbol, grid_type=config.grid_type,
            lower_price=config.lower_price, upper_price=config.upper_price, grids=config.grids,
            capital_quote=config.capital, max_open_orders=config.max_open_orders,
            trigger_price=trigger, status=GridStatus.DRAFT.value,
        )
        db.add(bot)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, 400)
    return {"id": bot.id, "status": bot.status, "levels": [{"index": x.index, "price": str(x.price)} for x in preview.levels]}


@router.post("/bots/{bot_id}/start")
def start_grid_bot(request: Request, bot_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    try:
        guard(request, csrf_token)
        bot = get_bot(db, bot_id)
        if bot.trigger_price is not None:
            adapter = adapter_for(bot)
            ticker = adapter.ticker(bot.symbol)
            raw_price = ticker.get("last") or ticker.get("close")
            current_price = D(str(raw_price or "0"))
            if not current_price.is_finite() or current_price <= 0:
                raise ValueError("exchange ticker has no usable price; grid remains unstarted")
            levels = materialize_levels(db, bot)
            if current_price > bot.trigger_price:
                bot.status = GridStatus.ARMED.value
                db.commit()
                return {"id": bot.id, "status": bot.status, "levels": len(levels),
                        "submitted": 0, "open_orders": [], "trigger_price": str(bot.trigger_price),
                        "current_price": str(current_price)}
            bot.status = GridStatus.RUNNING.value
            results = execute_initial_grid(db, adapter, bot)
            db.commit()
        else:
            levels = start_grid(db, bot)
            db.commit()
            results = execute_initial_grid(db, adapter_for(bot), bot)
    except ValueError as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, 400)
    return {"id": bot.id, "status": bot.status, "levels": len(levels), "submitted": len(results), "open_orders": grid_open_order_ids(db, bot)}


@router.post("/bots/{bot_id}/pause")
def pause_grid_bot(request: Request, bot_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    try:
        guard(request, csrf_token)
        bot = get_bot(db, bot_id)
        canceled = pause_grid_execution(db, adapter_for(bot), bot)
    except ValueError as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, 400)
    return {"id": bot.id, "status": bot.status, "canceled": len(canceled), "open_orders": grid_open_order_ids(db, bot)}


@router.post("/bots/{bot_id}/resume")
def resume_grid_bot(request: Request, bot_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    try:
        guard(request, csrf_token)
        bot = get_bot(db, bot_id)
        results = resume_grid_execution(db, adapter_for(bot), bot)
    except ValueError as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, 400)
    return {"id": bot.id, "status": bot.status, "submitted": len(results), "open_orders": grid_open_order_ids(db, bot)}


@router.post("/bots/{bot_id}/stop")
def stop_grid_bot(request: Request, bot_id: int, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    try:
        guard(request, csrf_token)
        bot = get_bot(db, bot_id)
        canceled = stop_grid_execution(db, adapter_for(bot), bot)
    except ValueError as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, 400)
    return {"id": bot.id, "status": bot.status, "canceled": len(canceled), "open_orders": grid_open_order_ids(db, bot)}


@router.get("/bots")
def list_grid_bots(request: Request, db: Session = Depends(get_db)):
    from .main import auth

    if not auth(request):
        raise HTTPException(401, "Authentication required")
    bots = db.scalars(select(GridBot).order_by(GridBot.updated_at.desc())).all()
    return [{
        "id": bot.id,
        "status": bot.status,
        "symbol": bot.symbol,
        "environment": bot.environment,
        "lower_price": str(bot.lower_price),
        "upper_price": str(bot.upper_price),
        "grids": bot.grids,
        "capital_quote": str(bot.capital_quote),
        "trigger_price": str(bot.trigger_price) if bot.trigger_price is not None else None,
        "open_orders": grid_open_order_ids(db, bot),
    } for bot in bots]


@router.get("/bots/{bot_id}")
def grid_status(request: Request, bot_id: int, db: Session = Depends(get_db)):
    from .main import auth

    if not auth(request):
        from fastapi import HTTPException
        raise HTTPException(401, "Authentication required")
    bot = get_bot(db, bot_id)
    levels = db.scalars(select(GridLevel).where(GridLevel.bot_id == bot.id)).all()
    current_price = None
    price_error = None
    nearest_level = None
    try:
        ticker = adapter_for(bot).ticker(bot.symbol)
        raw_price = ticker.get("last") or ticker.get("close")
        if raw_price is not None and D(str(raw_price)) > 0:
            current_price = D(str(raw_price))
            if levels:
                nearest = min(levels, key=lambda row: abs(D(str(row.price)) - current_price))
                nearest_level = {
                    "index": nearest.level_index,
                    "price": str(nearest.price),
                    "distance_pct": str(abs(D(str(nearest.price)) - current_price) / current_price * D("100")),
                }
    except Exception as exc:
        price_error = str(exc)
    return {
        "id": bot.id, "status": bot.status, "symbol": bot.symbol,
        "environment": bot.environment, "lower_price": str(bot.lower_price),
        "upper_price": str(bot.upper_price), "grids": bot.grids,
        "capital_quote": str(bot.capital_quote), "trigger_price": str(bot.trigger_price) if bot.trigger_price is not None else None, "open_orders": grid_open_order_ids(db, bot),
        "current_price": str(current_price) if current_price is not None else None,
        "price_error": price_error, "nearest_level": nearest_level,
        "levels": [{"index": x.level_index, "price": str(x.price),
                    "buy_order_id": x.buy_order_id, "sell_order_id": x.sell_order_id}
                   for x in levels],
    }
