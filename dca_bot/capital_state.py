from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ExchangeOrderRecord, TestnetDealRecord

D = Decimal


def _quote_amount(order: ExchangeOrderRecord) -> D:
    filled = D(order.filled)
    price = D(order.average or order.price)
    return filled * price


def allocated_quote(session: Session) -> D:
    """Return quote capital currently reserved by active deals and live intents."""
    deal_rows = session.execute(
        select(TestnetDealRecord.base_qty, TestnetDealRecord.average_entry).where(
            TestnetDealRecord.status == "active"
        )
    ).all()
    deal_allocated = sum(
        (D(qty) * D(avg) for qty, avg in deal_rows if qty and avg),
        D("0"),
    )

    intent_rows = session.execute(
        select(ExchangeOrderRecord.amount, ExchangeOrderRecord.price, ExchangeOrderRecord.filled).where(
            ExchangeOrderRecord.status.in_(["intent", "open", "new", "partially_filled"]),
            ExchangeOrderRecord.side == "buy",
        )
    ).all()
    pending = sum(
        (max(D("0"), D(amount) - D(filled)) * D(price) for amount, price, filled in intent_rows),
        D("0"),
    )
    return deal_allocated + pending
