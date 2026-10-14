from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from .models import ExchangeOrderRecord, TestnetDealRecord

D = Decimal


def apply_entry_fill(session: Session, order: ExchangeOrderRecord, filled: D, average: D | None = None, remaining: D | None = None, status: str | None = None) -> TestnetDealRecord:
    """Apply the newly observed BUY fill exactly once and update its deal."""
    if order.side != "buy":
        raise ValueError("entry fill must be a BUY order")
    if filled < 0 or filled > D(order.amount):
        raise ValueError("filled quantity outside order bounds")
    previous = D(order.last_accounted_filled)
    if filled < previous:
        raise ValueError("exchange fill moved backwards")

    deal = session.get(TestnetDealRecord, order.deal_id)
    if deal is None:
        raise ValueError("entry order has no deal")

    delta = filled - previous
    if delta > 0:
        fill_price = average if average is not None and average > 0 else D(order.price)
        deal.base_qty = str(D(deal.base_qty) + delta)
        deal.quote_cost = str(D(deal.quote_cost) + delta * fill_price)
        deal.average_entry = str(D(deal.quote_cost) / D(deal.base_qty))

    order.filled = str(filled)
    order.last_accounted_filled = str(filled)
    order.remaining = str(max(D("0"), D(order.amount) - filled) if remaining is None else remaining)
    if status is not None:
        order.status = status
    elif filled == D(order.amount):
        order.status = "filled"
    elif filled > 0:
        order.status = "partially_filled"

    if filled == D(order.amount):
        deal.status = "active"
    elif filled > 0:
        deal.status = "pending"

    session.commit()
    return deal
