from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from .execution import OrderResult


D = Decimal


class LocalOrderStatus(StrEnum):
    INTENT = "intent"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    UNKNOWN = "unknown"
    RECONCILE_REQUIRED = "reconcile_required"


@dataclass(frozen=True)
class OrderSnapshot:
    client_order_id: str
    symbol: str
    side: str
    requested_amount: D
    requested_price: D
    exchange_order_id: str | None = None
    status: LocalOrderStatus = LocalOrderStatus.INTENT
    filled_amount: D = D("0")
    average_price: D | None = None


@dataclass(frozen=True)
class ReconcileDecision:
    action: str
    reason: str
    snapshot: OrderSnapshot | None = None


def apply_exchange_result(local: OrderSnapshot, result: OrderResult) -> OrderSnapshot:
    """Map an exchange observation into a monotonic local order state."""
    if result.symbol and result.symbol != local.symbol:
        raise ValueError("Exchange order symbol does not match local order")
    if result.side and result.side != local.side:
        raise ValueError("Exchange order side does not match local order")
    if result.filled < 0 or result.filled > local.requested_amount:
        raise ValueError("Exchange reported an invalid filled amount")

    if result.status == "closed":
        status = LocalOrderStatus.FILLED
    elif result.status == "canceled":
        status = LocalOrderStatus.CANCELED
    elif result.status == "open":
        status = (
            LocalOrderStatus.PARTIALLY_FILLED
            if result.filled > 0
            else LocalOrderStatus.OPEN
        )
    else:
        status = LocalOrderStatus.UNKNOWN

    return OrderSnapshot(
        client_order_id=local.client_order_id,
        symbol=local.symbol,
        side=local.side,
        requested_amount=local.requested_amount,
        requested_price=local.requested_price,
        exchange_order_id=result.order_id or local.exchange_order_id,
        status=status,
        filled_amount=result.filled,
        average_price=result.average,
    )


def reconcile_by_client_id(
    local: OrderSnapshot,
    exchange_orders: Iterable[OrderResult],
) -> ReconcileDecision:
    """Find an exchange order by client ID without ever creating a duplicate."""
    matches = [
        item for item in exchange_orders
        if item.client_order_id and item.client_order_id == local.client_order_id
    ]
    if len(matches) > 1:
        return ReconcileDecision(
            action="halt",
            reason="Multiple exchange orders share the same client order ID",
        )
    if not matches:
        return ReconcileDecision(
            action="query_required",
            reason="No exchange order matched the local client order ID",
            snapshot=local,
        )
    return ReconcileDecision(
        action="adopt",
        reason="Exchange order matched local client order ID",
        snapshot=apply_exchange_result(local, matches[0]),
    )


def should_submit(local: OrderSnapshot) -> bool:
    """Only an untouched intent may submit a new exchange order."""
    return local.status == LocalOrderStatus.INTENT and not local.exchange_order_id
