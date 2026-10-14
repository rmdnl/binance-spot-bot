from __future__ import annotations

import json
from decimal import Decimal as D
from uuid import uuid4

from sqlalchemy.orm import Session

from .execution import ExecutionAdapter, ExecutionError, OrderIntent
from .models import AuditEvent, ExchangeOrderRecord
from .order_state import LocalOrderStatus, OrderSnapshot, apply_exchange_result, should_submit


def new_client_order_id(strategy_id: int, deal_id: str | None = None) -> str:
    suffix = (deal_id or uuid4().hex).replace("-", "")[:20]
    return f"dca{strategy_id}-{suffix}-{uuid4().hex[:8]}"[:64]


def create_order_intent(
    session: Session,
    *,
    strategy_id: int,
    symbol: str,
    side: str,
    amount: D,
    price: D,
    deal_id: str | None = None,
    client_order_id: str | None = None,
) -> ExchangeOrderRecord:
    """Persist the intent before any network submission."""
    if side not in {"buy", "sell"}:
        raise ValueError("Unsupported order side")
    cid = client_order_id or new_client_order_id(strategy_id, deal_id)
    row = ExchangeOrderRecord(
        strategy_id=strategy_id,
        deal_id=deal_id,
        symbol=symbol,
        side=side,
        client_order_id=cid,
        status=LocalOrderStatus.INTENT.value,
        amount=str(amount),
        price=str(price),
        filled="0",
        remaining=str(amount),
        raw_json="{}",
    )
    session.add(row)
    session.flush()
    return row


def submit_persisted_order(session: Session, adapter: ExecutionAdapter, row: ExchangeOrderRecord) -> OrderSnapshot:
    """Submit exactly one persisted intent and make ambiguous results recoverable."""
    local = OrderSnapshot(
        client_order_id=row.client_order_id,
        symbol=row.symbol,
        side=row.side,
        requested_amount=D(row.amount),
        requested_price=D(row.price),
        exchange_order_id=row.exchange_order_id,
        status=LocalOrderStatus(row.status),
        filled_amount=D(row.filled),
        average_price=D(row.average) if row.average else None,
    )
    if not should_submit(local):
        raise ExecutionError(f"Order {row.client_order_id} is not an untouched intent")

    intent = OrderIntent(
        symbol=row.symbol,
        side=row.side,  # type: ignore[arg-type]
        amount=D(row.amount),
        price=D(row.price),
        client_order_id=row.client_order_id,
        post_only=True,
    )
    try:
        result = adapter.place_limit(intent)
    except Exception as exc:
        row.status = LocalOrderStatus.UNKNOWN.value
        session.add(AuditEvent(
            event_type="exchange_order_submission_ambiguous",
            detail=json.dumps({"order_id": row.id, "client_order_id": row.client_order_id}),
        ))
        session.commit()
        raise ExecutionError("Exchange submission was ambiguous; reconciliation required") from exc

    snapshot = apply_exchange_result(local, result)
    row.exchange_order_id = snapshot.exchange_order_id
    row.status = snapshot.status.value
    row.filled = str(snapshot.filled_amount)
    row.remaining = str(D(row.amount) - snapshot.filled_amount)
    row.average = str(snapshot.average_price) if snapshot.average_price is not None else None
    row.raw_json = json.dumps(result.raw, default=str)
    session.add(AuditEvent(
        event_type="exchange_order_submitted",
        detail=json.dumps({
            "order_id": row.id,
            "client_order_id": row.client_order_id,
            "exchange_order_id": row.exchange_order_id,
            "status": row.status,
        }),
    ))
    session.commit()
    return snapshot
