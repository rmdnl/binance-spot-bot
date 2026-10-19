from collections.abc import Callable
from decimal import Decimal
from typing import TypeVar

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .db import get_db
from .exchange import BinanceSpot
from .models import Environment, OrderRequest, OrderSide, OrderStatus, OrderType, SpotOrder, SpotProtection
from .order_service import cancel_order, create_intent, record_fills, reconcile_order, recover_or_submit

router = APIRouter(prefix="/api/spot", tags=["spot"])
D = Decimal
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}
T = TypeVar("T")


def _commit_order_transaction(
    db: Session,
    order_id: int,
    operation: Callable[[SpotOrder], T],
) -> tuple[SpotOrder, T]:
    """Retry a stale ORM write by replaying the whole rolled-back transaction.

    External operations must be idempotent: submit recovers by clientOrderId,
    while cancel/reconcile fetch the exchange's current order state.
    """
    for attempt in range(2):
        order = db.get(SpotOrder, order_id, populate_existing=True)
        if order is None:
            raise LookupError(f"Order {order_id} not found")
        try:
            result = operation(order)
            db.commit()
            return order, result
        except StaleDataError:
            db.rollback()
            if attempt == 1:
                raise
        except Exception:
            db.rollback()
            raise
    raise RuntimeError("stale order transaction retry exhausted")


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
    stop_loss_price: str | None = Form(None), take_profit_price: str | None = Form(None),
    csrf_token: str = Form(...), db: Session = Depends(get_db),
):
    require_auth(request)
    require_csrf(request, csrf_token)
    adapter = None
    protection_requested = bool((stop_loss_price or "").strip() or (take_profit_price or "").strip())
    try:
        env = Environment(environment.lower().strip())
        side_enum = OrderSide(side.lower().strip())
        type_enum = OrderType(order_type.lower().strip())
        amount_d = D(amount)
        price_d = D(price) if price else None
        if type_enum == OrderType.MARKET:
            price_d = None
        sl_d = D(stop_loss_price) if (stop_loss_price or "").strip() else None
        tp_d = D(take_profit_price) if (take_profit_price or "").strip() else None
        if not amount_d.is_finite() or amount_d <= 0:
            raise ValueError("amount must be finite and positive")
        if price_d is not None and (not price_d.is_finite() or price_d <= 0):
            raise ValueError("price must be finite and positive")
        for label, value in (("stop loss", sl_d), ("take profit", tp_d)):
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError(f"{label} must be finite and positive")
        if protection_requested:
            if side_enum != OrderSide.BUY:
                raise ValueError("SL/TP protection can only be attached to a manual BUY order")
            adapter = BinanceSpot(env)
            reference = price_d
            if type_enum == OrderType.MARKET:
                ticker = adapter.ticker(symbol.upper().strip())
                reference = D(str(ticker.get("last") or ticker.get("close") or "0"))
            if reference is None or not reference.is_finite() or reference <= 0:
                raise ValueError("cannot determine a valid entry reference price for SL/TP")
            if sl_d is not None and sl_d >= reference:
                raise ValueError("stop loss must be below the BUY entry/reference price")
            if tp_d is not None and tp_d <= reference:
                raise ValueError("take profit must be above the BUY entry/reference price")
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(400, f"Invalid order parameters: {exc}") from exc

    req = OrderRequest(env, symbol.upper().strip(), side_enum, type_enum, amount_d, price_d, None, type_enum == OrderType.LIMIT_MAKER)
    try:
        order = create_intent(db, req)
        protection = None
        if protection_requested:
            protection = SpotProtection(
                entry_order_id=order.id, environment=env.value, symbol=req.symbol,
                stop_loss_price=sl_d, take_profit_price=tp_d, status="waiting_entry",
            )
            db.add(protection)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Persist entry intent and protection policy before the exchange side effect.
    try:
        adapter = adapter or BinanceSpot(env)
        def submit_and_account(current: SpotOrder):
            result = recover_or_submit(db, adapter, current)
            record_fills(db, adapter, current)
            return result

        order, result = _commit_order_transaction(db, order.id, submit_and_account)
    except Exception as exc:
        db.rollback()
        current = db.get(SpotOrder, order.id)
        status = current.status if current else order.status
        return JSONResponse({"id": order.id, "status": status, "error": str(exc), "recoverable": True}, status_code=502)
    payload = order_response(result.order)
    if protection_requested:
        db.refresh(protection)
        payload["protection"] = {
            "id": protection.id, "status": protection.status,
            "stop_loss_price": str(protection.stop_loss_price) if protection.stop_loss_price is not None else None,
            "take_profit_price": str(protection.take_profit_price) if protection.take_profit_price is not None else None,
        }
    return payload


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

        def cancel_and_account(current: SpotOrder) -> None:
            cancel_order(db, adapter, current)
            record_fills(db, adapter, current)

        order, _ = _commit_order_transaction(db, order_id, cancel_and_account)
    except Exception as exc:
        db.rollback()
        current = db.get(SpotOrder, order_id)
        status = current.status if current else order.status
        return JSONResponse({"id": order_id, "status": status, "error": str(exc), "recoverable": True}, status_code=502)
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

        def reconcile_and_account(current: SpotOrder) -> None:
            reconcile_order(db, adapter, current)
            record_fills(db, adapter, current)

        order, _ = _commit_order_transaction(db, order_id, reconcile_and_account)
    except Exception as exc:
        db.rollback()
        current = db.get(SpotOrder, order_id)
        status = current.status if current else order.status
        return JSONResponse({"id": order_id, "status": status, "error": str(exc), "recoverable": True}, status_code=502)
    return order_response(order)


@router.get("/protections")
def list_spot_protections(request: Request, db: Session = Depends(get_db)):
    require_auth(request)
    rows = db.scalars(select(SpotProtection).order_by(SpotProtection.updated_at.desc()).limit(100)).all()
    return [{
        "id": row.id, "entry_order_id": row.entry_order_id, "exit_order_id": row.exit_order_id,
        "environment": row.environment, "symbol": row.symbol, "status": row.status,
        "stop_loss_price": str(row.stop_loss_price) if row.stop_loss_price is not None else None,
        "take_profit_price": str(row.take_profit_price) if row.take_profit_price is not None else None,
        "protected_quantity": str(row.protected_quantity),
        "last_price": str(row.last_price) if row.last_price is not None else None,
        "trigger_reason": row.trigger_reason, "last_error": row.last_error,
    } for row in rows]
