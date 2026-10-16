from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .db import get_db
from .exchange import BinanceSpot
from .models import Environment, OrderRequest, OrderSide, OrderStatus, OrderType, SpotOrder
from .order_service import cancel_order, create_intent, record_fills, reconcile_order, recover_or_submit

router = APIRouter(prefix="/api/spot", tags=["spot"])
D = Decimal
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}


def require_auth(request: Request) -> None:
    from .main import auth
    if not auth(request):
        raise HTTPException(401, "Authentication required")


def require_csrf(request: Request, token: str) -> None:
    from .main import csrf_valid
    if not csrf_valid(token, request):
        raise HTTPException(403, "Invalid CSRF token")


def order_response(order: SpotOrder) -> dict:
    return {
        "id": order.id, "environment": order.environment, "symbol": order.symbol,
        "side": order.side, "type": order.order_type, "status": order.status,
        "amount": str(order.amount), "filled": str(order.filled_amount),
        "price": str(order.price) if order.price is not None else None,
        "average_price": str(order.average_price) if order.average_price is not None else None,
        "exchange_order_id": order.exchange_order_id, "client_order_id": order.client_order_id,
        "error": order.error_message,
    }


@router.post("/orders")
def create_spot_order(
    request: Request,
    environment: str = Form(...), symbol: str = Form(...), side: str = Form(...),
    order_type: str = Form(...), amount: str = Form(...), price: str | None = Form(None),
    csrf_token: str = Form(...), db: Session = Depends(get_db),
):
    require_auth(request)
    require_csrf(request, csrf_token)
    try:
        env = Environment(environment.lower().strip())
        side_enum = OrderSide(side.lower().strip())
        type_enum = OrderType(order_type.lower().strip())
        amount_d = D(amount)
        price_d = D(price) if price else None
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(400, f"Invalid order parameters: {exc}") from exc
    req = OrderRequest(env, symbol.upper().strip(), side_enum, type_enum, amount_d, price_d, None, type_enum == OrderType.LIMIT_MAKER)

    # Transaction 1: make the intent durable before any Binance network side effect.
    try:
        order = create_intent(db, req)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Binance I/O is deliberately outside the DB transaction. clientOrderId makes retry/recovery idempotent.
    try:
        adapter = BinanceSpot(env)
        result = recover_or_submit(db, adapter, order)
        db.commit()  # Transaction 2: persist the exchange acknowledgement.
    except Exception as exc:
        db.rollback()
        return JSONResponse({"id": order.id, "status": order.status, "error": str(exc), "recoverable": True}, status_code=502)
    return order_response(result.order)


@router.post("/orders/{order_id}/cancel")
def cancel_spot_order(order_id: int, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_auth(request)
    require_csrf(request, csrf_token)
    order = db.get(SpotOrder, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if order.status not in ACTIVE:
        raise HTTPException(409, f"Order is not cancelable from {order.status}")
    try:
        adapter = BinanceSpot(Environment(order.environment))
        cancel_order(db, adapter, order)
        record_fills(db, adapter, order)
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse({"id": order.id, "status": order.status, "error": str(exc), "recoverable": True}, status_code=502)
    return order_response(order)


@router.post("/orders/{order_id}/reconcile")
def reconcile(order_id: int, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_auth(request)
    require_csrf(request, csrf_token)
    order = db.get(SpotOrder, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    try:
        adapter = BinanceSpot(Environment(order.environment))
        reconcile_order(db, adapter, order)
        record_fills(db, adapter, order)
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse({"id": order.id, "status": order.status, "error": str(exc), "recoverable": True}, status_code=502)
    return order_response(order)
