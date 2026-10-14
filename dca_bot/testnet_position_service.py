from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import AuditEvent, ExchangeOrderRecord, TestnetDealRecord
from .position import Position

D = Decimal


def sync_order_fill(session: Session, deal: TestnetDealRecord, order: ExchangeOrderRecord) -> D:
    """Apply only the unaccounted exchange fill and persist its checkpoint."""
    previous = D(order.last_accounted_filled or "0")
    current = D(order.filled or "0")
    if current < previous:
        raise ValueError("exchange fill regressed")
    delta = current - previous
    if delta == 0:
        return D("0")

    position = Position(deal.symbol, D(deal.base_qty), D(deal.quote_cost))
    # For an incremental fill, use the exchange-reported average when present.
    # This is safe for cumulative average fields because the delta quantity is
    # applied at the latest reported average only when the order has progressed.
    price = D(order.average or order.price)
    realized = position.apply_order_delta(order.side, previous, current, price)

    deal.base_qty = str(position.base_qty)
    deal.quote_cost = str(position.quote_cost)
    deal.average_entry = str(position.average_entry) if position.average_entry is not None else None
    deal.realized_pnl = str(D(deal.realized_pnl) + realized)
    if position.base_qty == 0 and order.side == "sell":
        deal.status = "closed"

    order.last_accounted_filled = str(current)
    order.state_json = json.dumps({"last_accounted_filled": str(current)})
    session.add(AuditEvent(event_type="testnet_fill_accounted", detail=f"deal={deal.id} order={order.id} delta={delta} side={order.side}"))
    session.commit()
    return delta
