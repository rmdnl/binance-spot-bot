from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .db import get_db
from .models import Environment, GridBot, GridStatus
from .grid_service import initial_grid_orders
from .main import auth, csrf_valid

router = APIRouter(prefix="/api/grid", tags=["grid"])
D = Decimal


def guard(request: Request, csrf_token: str) -> None:
    if not auth(request):
        from fastapi import HTTPException
        raise HTTPException(401, "Authentication required")
    if not csrf_valid(csrf_token, request):
        raise ValueError("Invalid CSRF token")


@router.post("/bots")
def create_grid(
    request: Request, environment: str = Form(...), symbol: str = Form(...), lower_price: str = Form(...),
    upper_price: str = Form(...), grids: int = Form(...), capital_quote: str = Form(...),
    grid_type: str = Form("arithmetic"), max_open_orders: int = Form(20), csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try: guard(request, csrf_token)
    except ValueError as exc: return JSONResponse({"error": str(exc)}, 403)
    config = __import__("spot_bot.models", fromlist=["GridConfig"]).GridConfig(symbol.upper(), D(lower_price), D(upper_price), grids, D(capital_quote), grid_type.lower(), max_open_orders)
    preview = initial_grid_orders(config)
    bot = GridBot(environment=Environment(environment.lower()).value, symbol=config.symbol, grid_type=config.grid_type, lower_price=config.lower_price, upper_price=config.upper_price, grids=config.grids, capital_quote=config.capital, max_open_orders=config.max_open_orders, status=GridStatus.DRAFT.value)
    db.add(bot); db.commit()
    return {"id": bot.id, "status": bot.status, "levels":[{"index":x.index,"price":str(x.price)} for x in preview.levels]}
