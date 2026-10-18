from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .exchange import BinanceSpot
from .grid_engine import execute_initial_grid, pause_grid_execution, resume_grid_execution, stop_grid_execution
from .grid_service import grid_open_order_ids, initial_grid_orders, start_grid
from .main import auth, csrf_valid
from .models import Environment, GridBot, GridConfig, GridLevel, GridStatus

router = APIRouter(prefix="/api/grid", tags=["grid"])
D = Decimal


def guard(request: Request, csrf_token: str) -> None:
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
    grid_type: str = Form("arithmetic"), max_open_orders: int = Form(20), csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        guard(request, csrf_token)
        env = Environment(environment.lower())
        config = GridConfig(symbol.upper(), D(lower_price), D(upper_price), grids, D(capital_quote), grid_type.lower(), max_open_orders)
        preview = initial_grid_orders(config)
        bot = GridBot(
            environment=env.value, symbol=config.symbol, grid_type=config.grid_type,
            lower_price=config.lower_price, upper_price=config.upper_price, grids=config.grids,
            capital_quote=config.capital, max_open_orders=config.max_open_orders,
            status=GridStatus.DRAFT.value,
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


@router.get("/bots/{bot_id}")
def grid_status(request: Request, bot_id: int, db: Session = Depends(get_db)):
    if not auth(request):
        from fastapi import HTTPException
        raise HTTPException(401, "Authentication required")
    bot = get_bot(db, bot_id)
    levels = db.scalars(select(GridLevel).where(GridLevel.bot_id == bot.id)).all()
    return {"id": bot.id, "status": bot.status, "symbol": bot.symbol, "lower_price": str(bot.lower_price), "upper_price": str(bot.upper_price), "grids": bot.grids, "capital_quote": str(bot.capital_quote), "open_orders": grid_open_order_ids(db, bot), "levels": [{"index": x.level_index, "price": str(x.price), "buy_order_id": x.buy_order_id, "sell_order_id": x.sell_order_id} for x in levels]}
