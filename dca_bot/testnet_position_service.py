from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import AuditEvent, ExchangeOrderRecord
from .position import Position
from .testnet_models import TestnetDealRecord

D = Decimal


def sync_order_fill(session: Session, deal: TestnetDealRecord, order: ExchangeOrderRecord) -> D:
    """Apply only the newly observed exchange fill to durable position state."""
    previous = D(order.state_filled_before_sync or "0") if hasattr(order, "state_filled_before_sync") else D(order.filled or "0")
    current = D(order.filled or "0")
    if current < previous:
        raise ValueError("exchange fill regressed")
    delta = current - previous
    if delta == 0:
        return D("0")

    position = Position(deal.symbol, D(deal.base_qty), D(deal.quote_cost))
    price = D(order.average or order.price)
    realized = position.apply_order_delta(order.side, previous, current, price)

    deal.base_qty = str(position.base_qty)
    deal.quote_cost = str(position.quote_cost)
    deal.average_entry = str(position.average_entry) if position.average_entry is not None else None
    deal.realized_pnl = str(D(deal.realized_pnl) + realized)
    if position.base_qty == 0 and order.side == "sell":
        deal.status = "closed"

    order.state_json = json.dumps({"last_accounted_filled": str(current)})
    session.add(AuditEvent(event_type="testnet_fill_accounted", detail=f"deal={deal.id} order={order.id} delta={delta} side={order.side}"))
    session.commit()
    return delta
