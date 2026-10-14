from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .execution import ExecutionAdapter
from .fill_state import apply_entry_fill
from .models import ExchangeOrderRecord

D = Decimal


@dataclass(frozen=True)
class ReconcileStats:
    checked: int
    updated: int
    failed: int


def reconcile_testnet_orders(session: Session, adapter: ExecutionAdapter, strategy_id: int) -> ReconcileStats:
    rows = session.execute(
        select(ExchangeOrderRecord).where(
            ExchangeOrderRecord.strategy_id == strategy_id,
            ExchangeOrderRecord.side == "buy",
            ExchangeOrderRecord.status.in_(["intent", "new", "open", "partially_filled"]),
        ).order_by(ExchangeOrderRecord.id.asc())
    ).scalars().all()
    updated = failed = 0
    for record in rows:
        try:
            result = adapter.fetch_order_by_client_id(record.symbol, record.client_order_id)
            if not result:
                continue
            order = result[0]
            apply_entry_fill(
                session, record, D(order.filled),
                D(order.average) if order.average is not None else None,
                D(order.remaining), order.status,
            )
            record.exchange_order_id = order.order_id or record.exchange_order_id
            record.raw_json = str(order.raw)
            session.commit()
            updated += 1
        except Exception:
            session.rollback()
            failed += 1
    return ReconcileStats(len(rows), updated, failed)
