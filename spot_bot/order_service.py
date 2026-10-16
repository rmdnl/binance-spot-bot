from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .exchange import BinanceSpot, ExchangeSafetyError
from .models import Environment, OrderRequest, OrderSide, OrderStatus, OrderType, SpotFill, SpotOrder

D = Decimal


@dataclass(frozen=True)
class SubmitResult:
    order: SpotOrder
    exchange: dict[str, Any] | None


def _client_id() -> str:
    import secrets
    return "spot-" + secrets.token_hex(15)


def create_intent(db: Session, request: OrderRequest) -> SpotOrder:
    if request.amount <= 0:
        raise ValueError("amount must be positive")
    if request.order_type != OrderType.MARKET and (request.price is None or request.price <= 0):
        raise ValueError("limit orders require a positive price")
    client_id = request.client_order_id or _client_id()
    if len(client_id) > 36:
        raise ValueError("client_order_id exceeds Binance 36 character limit")
    order = SpotOrder(
        environment=request.environment.value,
        symbol=request.symbol,
        side=request.side.value,
        order_type=request.order_type.value,
        status=OrderStatus.NEW.value,
        amount=request.amount,
        price=request.price,
        client_order_id=client_id,
    )
    db.add(order)
    db.flush()
    return order


def submit_order(db: Session, adapter: BinanceSpot, order: SpotOrder, post_only: bool = False) -> SubmitResult:
    if order.status not in {OrderStatus.NEW.value, OrderStatus.REJECTED.value}:
        raise ValueError(f"order {order.id} is not submittable from {order.status}")
    request = OrderRequest(
        environment=Environment(order.environment),
        symbol=order.symbol,
        side=OrderSide(order.side),
        order_type=OrderType(order.order_type),
        amount=order.amount,
        price=order.price,
        client_order_id=order.client_order_id,
        post_only=post_only or order.order_type == OrderType.LIMIT_MAKER.value,
    )
    try:
        raw = adapter.create_order(request)
    except Exception as exc:
        order.status = OrderStatus.REJECTED.value
        order.error_message = str(exc)[:500]
        db.flush()
        raise
    order.exchange_order_id = str(raw.get("id")) if raw.get("id") is not None else None
    order.status = _map_status(raw.get("status"))
    order.filled_amount = D(str(raw.get("filled") or "0"))
    avg = raw.get("average")
    order.average_price = D(str(avg)) if avg is not None else None
    db.flush()
    return SubmitResult(order, raw)


def _map_status(status: str | None) -> str:
    return {
        "open": OrderStatus.OPEN.value,
        "closed": OrderStatus.FILLED.value,
        "canceled": OrderStatus.CANCELED.value,
        "expired": OrderStatus.EXPIRED.value,
        "rejected": OrderStatus.REJECTED.value,
    }.get((status or "").lower(), OrderStatus.UNKNOWN.value)


def reconcile_order(db: Session, adapter: BinanceSpot, order: SpotOrder) -> SpotOrder:
    if not order.exchange_order_id:
        raise ValueError("cannot reconcile order without exchange_order_id")
    raw = adapter.exchange.fetch_order(order.exchange_order_id, order.symbol)
    order.status = _map_status(raw.get("status"))
    order.filled_amount = D(str(raw.get("filled") or "0"))
    avg = raw.get("average")
    order.average_price = D(str(avg)) if avg is not None else order.average_price
    fee = raw.get("fee") or {}
    if fee.get("currency") == adapter.market(order.symbol).get("quote") and fee.get("cost") is not None:
        order.fee_quote = D(str(fee["cost"]))
    db.flush()
    return order


def record_fills(db: Session, adapter: BinanceSpot, order: SpotOrder) -> int:
    if not order.exchange_order_id:
        raise ValueError("cannot fetch fills without exchange_order_id")
    trades = adapter.exchange.fetch_my_trades(order.symbol, limit=100)
    existing = set(db.scalars(select(SpotFill.exchange_trade_id).where(SpotFill.order_id == order.id)).all())
    count = 0
    for trade in trades:
        if str(trade.get("order")) != str(order.exchange_order_id):
            continue
        trade_id = str(trade.get("id")) if trade.get("id") is not None else None
        if trade_id and trade_id in existing:
            continue
        amount = D(str(trade.get("amount") or "0")); price = D(str(trade.get("price") or "0"))
        if amount <= 0 or price <= 0:
            continue
        cost = D(str(trade.get("cost") or amount * price))
        fee = trade.get("fee") or {}
        fill = SpotFill(order_id=order.id, symbol=order.symbol, side=order.side, price=price, amount=amount, quote_amount=cost, fee=D(str(fee.get("cost") or "0")), fee_currency=str(fee.get("currency") or ""), exchange_trade_id=trade_id)
        db.add(fill); count += 1
    db.flush()
    return count
