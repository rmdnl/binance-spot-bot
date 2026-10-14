from __future__ import annotations

from sqlalchemy.orm import Session

from .models import ExchangeOrderRecord


PENDING_STATUSES = ("intent", "new", "open", "partially_filled")


def pending_entry_intents(session: Session, strategy_id: int) -> list[ExchangeOrderRecord]:
    """Return durable BUY intents that still need exchange reconciliation/submission."""
    return list(
        session.query(ExchangeOrderRecord)
        .filter(
            ExchangeOrderRecord.strategy_id == strategy_id,
            ExchangeOrderRecord.side == "buy",
            ExchangeOrderRecord.status.in_(PENDING_STATUSES),
        )
        .order_by(ExchangeOrderRecord.id.asc())
        .all()
    )
