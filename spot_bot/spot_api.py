from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .db import get_db
from .exchange import BinanceSpot
from .models import Environment, OrderRequest, OrderSide, OrderType, SpotOrder
from .order_service import create_intent, submit_order, reconcile_order, record_fills
from .risk import RiskLimits, RiskSnapshot, check_risk

router = APIRouter(prefix="/api/spot", tags=["spot"])
D = Decimal


def require_auth(request: Request) -> None:
    from .main import auth
    if not auth(request):
        from fastapi import HTTPException
        raise HTTPException(401, "Authentication required")


@router.post("/orders")
def create_spot_order(
    request: Request,
    environment: str = Form(...),
    symbol: str = Form(...),
    side: str = Form(...),
    order_type: str = Form(...),
    amount: str = Form(...),
    price: str | None = Form(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_auth(request)
    from .main import csrf_valid
    if not csrf_valid(csrf_token, request):
        return JSONResponse({"error": "Invalid CSRF token"}, status_code=403)
    env = Environment(environment.lower())
    req = OrderRequest(env, symbol.upper(), OrderSide(side.lower()), OrderType(order_type.lower()), D(amount), D(price) if price else None, None, order_type.lower() == "limit_maker")
    order = create_intent(db, req)
    db.commit()
    try:
        adapter = BinanceSpot(env)
        result = submit_order(db, adapter, order)
        db.commit()
    except Exception:
        db.commit()
        return JSONResponse({"id": order.id, "status": order.status, "error": order.error_message}, status_code=502)
    return {"id": result.order.id, "status": result.order.status, "exchange_order_id": result.order.exchange_order_id, "client_order_id": result.order.client_order_id}


@router.post("/orders/{order_id}/reconcile")
def reconcile(order_id: int, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_auth(request)
    from .main import csrf_valid
    if not csrf_valid(csrf_token, request): return JSONResponse({"error": "Invalid CSRF token"}, status_code=403)
    order = db.get(SpotOrder, order_id)
    if not order: return JSONResponse({"error": "Order not found"}, status_code=404)
    adapter = BinanceSpot(Environment(order.environment))
    reconcile_order(db, adapter, order)
    record_fills(db, adapter, order)
    db.commit()
    return {"id": order.id, "status": order.status, "filled": str(order.filled_amount)}
