from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import ccxt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .exchange import BinanceSpot
from .models import Environment, OrderRequest, OrderSide, OrderStatus, OrderType, SpotFill, SpotOrder

D = Decimal
FEE_SCALE = D("0.00000001")
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}

# Exchange status updates must never move a local order backwards or resurrect
# a terminal order after restart/reconciliation.
_ALLOWED_TRANSITIONS = {
    OrderStatus.NEW.value: {
        OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value,
        OrderStatus.FILLED.value, OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value,
        OrderStatus.REJECTED.value,
    },
    OrderStatus.OPEN.value: {
        OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value,
        OrderStatus.FILLED.value, OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value,
    },
    OrderStatus.PARTIALLY_FILLED.value: {
        OrderStatus.PARTIALLY_FILLED.value, OrderStatus.FILLED.value,
        OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value,
    },
    OrderStatus.FILLED.value: {OrderStatus.FILLED.value},
    OrderStatus.CANCELED.value: {OrderStatus.CANCELED.value},
    OrderStatus.EXPIRED.value: {OrderStatus.EXPIRED.value},
    OrderStatus.REJECTED.value: {OrderStatus.REJECTED.value},
    OrderStatus.UNKNOWN.value: {OrderStatus.UNKNOWN.value},
}


@dataclass(frozen=True)
class SubmitResult:
    order: SpotOrder
    exchange: dict[str, Any] | None


def _client_id() -> str:
    # Binance Spot clientOrderId is limited to 36 characters.
    # Keep the format explicit: 5-char prefix + 31 hex characters = exactly 36.
    import secrets
    return "spot-" + secrets.token_hex(16)[:31]


def create_intent(db: Session, request: OrderRequest) -> SpotOrder:
    if request.amount <= 0:
        raise ValueError("amount must be positive")
    if request.order_type != OrderType.MARKET and (request.price is None or request.price <= 0):
        raise ValueError("limit orders require a positive price")
    client_id = request.client_order_id or _client_id()
    if len(client_id) > 36:
        raise ValueError("client_order_id exceeds Binance 36 character limit")
    duplicate = db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == client_id))
    if duplicate:
        raise ValueError(f"client_order_id already exists for local order {duplicate.id}")
    order = SpotOrder(
        environment=request.environment.value, symbol=request.symbol.upper(), side=request.side.value,
        order_type=request.order_type.value, status=OrderStatus.NEW.value, amount=request.amount,
        price=request.price, client_order_id=client_id,
    )
    db.add(order)
    db.flush()
    return order


def _apply_exchange_state(order: SpotOrder, raw: dict[str, Any]) -> None:
    incoming = _map_status(raw.get("status"))
    allowed = _ALLOWED_TRANSITIONS.get(order.status, {order.status})
    if incoming not in allowed:
        raise ValueError(
            f"invalid order state transition: {order.status} -> {incoming}"
        )
    order.exchange_order_id = str(raw.get("id")) if raw.get("id") is not None else order.exchange_order_id
    order.status = incoming
    order.filled_amount = D(str(raw.get("filled") or "0"))
    avg = raw.get("average")
    if avg is not None:
        order.average_price = D(str(avg))
    fee = raw.get("fee") or {}
    if fee.get("cost") is not None:
        quote = order.symbol.split("/")[1]
        if str(fee.get("currency") or "") == quote:
            order.fee_quote = D(str(fee["cost"])).quantize(FEE_SCALE)


def submit_order(db: Session, adapter: BinanceSpot, order: SpotOrder, post_only: bool = False) -> SubmitResult:
    if order.status not in {OrderStatus.NEW.value, OrderStatus.REJECTED.value}:
        raise ValueError(f"order {order.id} is not submittable from {order.status}")
    request = OrderRequest(
        Environment(order.environment), order.symbol, OrderSide(order.side), OrderType(order.order_type),
        order.amount, order.price, order.client_order_id,
        post_only or order.order_type == OrderType.LIMIT_MAKER.value,
    )
    try:
        adapter.validate_order(request)
        raw = adapter.create_order(request)
    except Exception as exc:
        order.status = OrderStatus.REJECTED.value
        order.error_message = str(exc)[:500]
        db.flush()
        raise
    _apply_exchange_state(order, raw)
    db.flush()
    return SubmitResult(order, raw)


def recover_or_submit(db: Session, adapter: BinanceSpot, order: SpotOrder, post_only: bool = False) -> SubmitResult:
    if order.status not in {OrderStatus.NEW.value, OrderStatus.REJECTED.value}:
        raise ValueError(f"order {order.id} is not submittable from {order.status}")
    try:
        existing = adapter.fetch_order_by_client_id(order.symbol, order.client_order_id)
    except ccxt.OrderNotFound:
        existing = None
    except Exception:
        # Unknown exchange/network errors are NOT treated as "not found". Never risk a duplicate order.
        raise
    if existing:
        _apply_exchange_state(order, existing)
        record_fills(db, adapter, order)
        db.flush()
        return SubmitResult(order, existing)
    return submit_order(db, adapter, order, post_only)


def _map_status(status: str | None) -> str:
    return {
        "new": OrderStatus.NEW.value, "open": OrderStatus.OPEN.value,
        "closed": OrderStatus.FILLED.value, "canceled": OrderStatus.CANCELED.value,
        "expired": OrderStatus.EXPIRED.value, "rejected": OrderStatus.REJECTED.value,
        "partially_filled": OrderStatus.PARTIALLY_FILLED.value,
    }.get((status or "").lower(), OrderStatus.UNKNOWN.value)


def reconcile_order(db: Session, adapter: BinanceSpot, order: SpotOrder) -> SpotOrder:
    if order.status not in ACTIVE:
        return order
    raw = adapter.fetch_order(order.exchange_order_id, order.symbol) if order.exchange_order_id else adapter.fetch_order_by_client_id(order.symbol, order.client_order_id)
    _apply_exchange_state(order, raw)
    record_fills(db, adapter, order)
    db.flush()
    return order


def cancel_order(db: Session, adapter: BinanceSpot, order: SpotOrder) -> SpotOrder:
    if order.status not in ACTIVE:
        raise ValueError(f"order {order.id} cannot be canceled from {order.status}")
    if not order.exchange_order_id:
        raise ValueError("cannot cancel order without exchange_order_id")
    try:
        raw = adapter.cancel_order(order.exchange_order_id, order.symbol)
    except (ccxt.OrderNotFound, ccxt.InvalidOrder):
        # A cancel race is not proof that no fill occurred. Re-read the
        # exchange order and let its terminal status/fill quantity win.
        raw = adapter.fetch_order(order.exchange_order_id, order.symbol)
    _apply_exchange_state(order, raw)
    record_fills(db, adapter, order)
    if order.status not in {OrderStatus.CANCELED.value, OrderStatus.FILLED.value, OrderStatus.EXPIRED.value}:
        order.status = OrderStatus.CANCELED.value
    db.flush()
    return order


def record_fills(db: Session, adapter: BinanceSpot, order: SpotOrder) -> int:
    if not order.exchange_order_id:
        return 0
    trades = adapter.recent_trades(order.symbol, limit=100)
    existing = set(db.scalars(select(SpotFill.exchange_trade_id).where(SpotFill.order_id == order.id)).all())
    count = 0
    for trade in trades:
        if str(trade.get("order")) != str(order.exchange_order_id):
            continue
        trade_id = str(trade.get("id")) if trade.get("id") is not None else None
        if not trade_id or trade_id in existing:
            continue
        amount, price = D(str(trade.get("amount") or "0")), D(str(trade.get("price") or "0"))
        if amount <= 0 or price <= 0:
            continue
        fee = trade.get("fee") or {}
        db.add(SpotFill(
            order_id=order.id, symbol=order.symbol, side=order.side, price=price, amount=amount,
            quote_amount=D(str(trade.get("cost") or amount * price)), fee=D(str(fee.get("cost") or "0")),
            fee_currency=str(fee.get("currency") or ""), exchange_trade_id=trade_id,
        ))
        existing.add(trade_id)
        count += 1

    db.flush()

    # The fill ledger is the canonical source for quote-denominated fees.
    quote = order.symbol.split("/")[1]
    fills = db.scalars(select(SpotFill).where(SpotFill.order_id == order.id)).all()
    quote_fee = sum((D(str(fill.fee)).quantize(FEE_SCALE) for fill in fills if fill.fee_currency == quote), D("0"))
    if fills:
        order.fee_quote = quote_fee.quantize(FEE_SCALE)

    return count
