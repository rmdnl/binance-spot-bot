from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import ccxt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .exchange import BinanceSpot
from .models import (
    Environment, InventoryApplication, OrderRequest, OrderSide,
    OrderStatus, OrderType, SpotFill, SpotOrder,
)
from .inventory_service import apply_fill
from .risk_service import assert_trading_allowed

D = Decimal
FEE_SCALE = D("0.00000001")
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}

_ALLOWED_TRANSITIONS = {
    OrderStatus.NEW.value: {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value, OrderStatus.FILLED.value, OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value, OrderStatus.REJECTED.value},
    OrderStatus.OPEN.value: {OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value, OrderStatus.FILLED.value, OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value},
    OrderStatus.PARTIALLY_FILLED.value: {OrderStatus.PARTIALLY_FILLED.value, OrderStatus.FILLED.value, OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value},
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
    import secrets
    return "spot-" + secrets.token_hex(16)[:31]

def create_intent(db: Session, request: OrderRequest) -> SpotOrder:
    assert_trading_allowed(db)
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
    order = SpotOrder(environment=request.environment.value, symbol=request.symbol.upper(), side=request.side.value, order_type=request.order_type.value, status=OrderStatus.NEW.value, amount=request.amount, price=request.price, client_order_id=client_id)
    db.add(order)
    db.flush()
    return order

def _apply_exchange_state(order: SpotOrder, raw: dict[str, Any], *, allow_rejected_retry: bool = False) -> None:
    incoming = _map_status(raw.get("status"))
    current = OrderStatus.NEW.value if allow_rejected_retry and order.status == OrderStatus.REJECTED.value else order.status
    if incoming not in _ALLOWED_TRANSITIONS.get(current, {current}):
        raise ValueError(f"invalid order state transition: {order.status} -> {incoming}")
    order.exchange_order_id = str(raw.get("id")) if raw.get("id") is not None else order.exchange_order_id
    order.status = incoming
    order.filled_amount = D(str(raw.get("filled") or "0"))
    avg = raw.get("average")
    if avg is not None:
        order.average_price = D(str(avg))
    fee = raw.get("fee") or {}
    if fee.get("cost") is not None and str(fee.get("currency") or "") == order.symbol.split("/")[1]:
        order.fee_quote = D(str(fee["cost"])).quantize(FEE_SCALE)

def submit_order(db: Session, adapter: BinanceSpot, order: SpotOrder, post_only: bool = False) -> SubmitResult:
    assert_trading_allowed(db)
    if order.status not in {OrderStatus.NEW.value, OrderStatus.REJECTED.value}:
        raise ValueError(f"order {order.id} is not submittable from {order.status}")
    request = OrderRequest(Environment(order.environment), order.symbol, OrderSide(order.side), OrderType(order.order_type), order.amount, order.price, order.client_order_id, post_only or order.order_type == OrderType.LIMIT_MAKER.value)
    retrying_rejected = order.status == OrderStatus.REJECTED.value
    try:
        adapter.validate_order(request)
        raw = adapter.create_order(request)
    except Exception as exc:
        # A transport failure after submission is ambiguous: the exchange may
        # have accepted the order even though this process never received its
        # response. Keep the durable intent retryable so recovery can query by
        # client_order_id instead of treating the order as definitely rejected.
        ambiguous = isinstance(
            exc,
            (
                ccxt.NetworkError,
                ccxt.RequestTimeout,
                ccxt.ExchangeNotAvailable,
                ccxt.DDoSProtection,
            ),
        )
        if not ambiguous:
            order.status = OrderStatus.REJECTED.value
            order.error_message = str(exc)[:500]
        db.flush()
        raise
    _apply_exchange_state(order, raw, allow_rejected_retry=retrying_rejected)
    db.flush()
    return SubmitResult(order, raw)

def recover_or_submit(db: Session, adapter: BinanceSpot, order: SpotOrder, post_only: bool = False) -> SubmitResult:
    assert_trading_allowed(db)
    if order.status not in {OrderStatus.NEW.value, OrderStatus.REJECTED.value}:
        raise ValueError(f"order {order.id} is not submittable from {order.status}")
    try:
        existing = adapter.fetch_order_by_client_id(order.symbol, order.client_order_id)
    except ccxt.OrderNotFound:
        existing = None
    except Exception:
        raise
    if existing:
        _apply_exchange_state(order, existing, allow_rejected_retry=order.status == OrderStatus.REJECTED.value)
        record_fills(db, adapter, order)
        db.flush()
        return SubmitResult(order, existing)

    try:
        return submit_order(db, adapter, order, post_only)
    except Exception:
        # Another worker may have submitted this same durable intent after our
        # first lookup, or the exchange may have accepted our request before a
        # timeout/connection loss. Re-check the stable client ID before exposing
        # the submit error to callers; never issue a second blind submission.
        try:
            existing = adapter.fetch_order_by_client_id(order.symbol, order.client_order_id)
        except Exception:
            # The original submission result remains authoritative for the
            # caller. A future reconcile/restart can safely retry the lookup.
            existing = None

        if not existing:
            raise

        _apply_exchange_state(
            order,
            existing,
            allow_rejected_retry=order.status == OrderStatus.REJECTED.value,
        )
        record_fills(db, adapter, order)
        db.flush()
        return SubmitResult(order, existing)

def _map_status(status: str | None) -> str:
    return {"new": OrderStatus.NEW.value, "open": OrderStatus.OPEN.value, "closed": OrderStatus.FILLED.value, "canceled": OrderStatus.CANCELED.value, "expired": OrderStatus.EXPIRED.value, "rejected": OrderStatus.REJECTED.value, "partially_filled": OrderStatus.PARTIALLY_FILLED.value}.get((status or "").lower(), OrderStatus.UNKNOWN.value)

def reconcile_order(db: Session, adapter: BinanceSpot, order: SpotOrder) -> SpotOrder:
    if order.status not in ACTIVE:
        # Terminal orders can still have trades that become visible after the
        # cancel/fill response. The caller selects only candidates with a
        # reported fill amount not yet represented by persisted fills.
        if order.status in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELED.value,
            OrderStatus.EXPIRED.value,
        } and order.exchange_order_id:
            record_fills(db, adapter, order)
            db.flush()
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
        raw = adapter.fetch_order(order.exchange_order_id, order.symbol)
    _apply_exchange_state(order, raw)
    record_fills(db, adapter, order)
    if order.status not in {OrderStatus.CANCELED.value, OrderStatus.FILLED.value, OrderStatus.EXPIRED.value}:
        order.status = OrderStatus.CANCELED.value
    db.flush()
    return order

def record_fills(db: Session, adapter: BinanceSpot, order: SpotOrder) -> int:
    count = 0

    # Trade discovery may be unavailable for a local order without an exchange ID.
    # Still process previously persisted, unapplied fills below.
    if order.exchange_order_id:
        # Prefer an exchange-order-scoped query so a delayed fill cannot fall
        # out of the account's latest 100 trades before the next reconciliation.
        fetch_for_order = getattr(type(adapter), "trades_for_order", None)
        trades = (
            fetch_for_order(adapter, order.symbol, order.exchange_order_id)
            if callable(fetch_for_order)
            else adapter.recent_trades(order.symbol, limit=100)
        )
        existing = set(
            db.scalars(
                select(SpotFill.exchange_trade_id).where(SpotFill.order_id == order.id)
            ).all()
        )
        for trade in trades:
            if str(trade.get("order")) != str(order.exchange_order_id):
                continue
            trade_id = str(trade.get("id")) if trade.get("id") is not None else None
            if not trade_id or trade_id in existing:
                continue

            amount = D(str(trade.get("amount") or "0"))
            price = D(str(trade.get("price") or "0"))
            if amount <= 0 or price <= 0:
                continue

            fee = trade.get("fee") or {}
            fill = SpotFill(
                order_id=order.id,
                environment=order.environment,
                symbol=order.symbol,
                side=order.side,
                price=price,
                amount=amount,
                quote_amount=D(str(trade.get("cost") or amount * price)),
                fee=D(str(fee.get("cost") or "0")),
                fee_currency=str(fee.get("currency") or ""),
                fee_quote_value=None,
                exchange_trade_id=trade_id,
            )
            try:
                with db.begin_nested():
                    db.add(fill)
                    db.flush()
            except IntegrityError:
                existing.add(trade_id)
                continue

            existing.add(trade_id)
            count += 1

    db.flush()

    quote = order.symbol.split("/")[1]
    order_fills = db.scalars(
        select(SpotFill).where(SpotFill.order_id == order.id)
    ).all()
    if order_fills:
        quote_fee = sum(
            (
                D(str(fill.fee)).quantize(FEE_SCALE)
                for fill in order_fills
                if fill.fee_currency.upper() == quote.upper()
            ),
            D("0"),
        )
        order.fee_quote = quote_fee.quantize(FEE_SCALE)

    # Recover any unapplied fill for this environment/pair, not only newly
    # discovered fills. apply_fill is idempotent and shares the caller's
    # transaction, so fill persistence and accounting commit or roll back together.
    pending_fills = db.scalars(
        select(SpotFill)
        .join(SpotOrder, SpotOrder.id == SpotFill.order_id)
        .outerjoin(
            InventoryApplication,
            InventoryApplication.fill_id == SpotFill.id,
        )
        .where(
            SpotOrder.environment == order.environment,
            SpotFill.symbol == order.symbol,
            InventoryApplication.id.is_(None),
        )
        .order_by(SpotFill.timestamp, SpotFill.id)
    ).all()

    for fill in pending_fills:
        fee_currency = (fill.fee_currency or "").upper()
        base, quote_asset = (part.upper() for part in fill.symbol.split("/", 1))
        if D(str(fill.fee or "0")) > 0 and fee_currency not in {base, quote_asset} and fill.fee_quote_value is None:
            # The fee currency conversion is captured at ingestion/recovery time.
            # If the market is unavailable, fail the transaction and retry later
            # rather than silently omitting a real trading cost from PNL.
            fee_symbol = f"{fee_currency}/{quote_asset}"
            ticker = adapter.ticker(fee_symbol)
            last = ticker.get("last") if ticker else None
            if last is None or not D(str(last)).is_finite() or D(str(last)) <= 0:
                raise RuntimeError(f"cannot value third-asset fee {fee_currency} in {quote_asset}")
            fill.fee_quote_value = D(str(fill.fee)) * D(str(last))
            db.flush()
        apply_fill(db, fill)

    db.flush()
    return count
