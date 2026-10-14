from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any

from sqlalchemy.orm import Session

from .execution import ExecutionAdapter, ExecutionError, OrderIntent, OrderResult
from .models import AuditEvent, ExchangeOrderRecord


def _result_to_json(result: OrderResult) -> str:
    return json.dumps({
        "order_id": result.order_id,
        "client_order_id": result.client_order_id,
        "symbol": result.symbol,
        "side": result.side,
        "status": result.status,
        "amount": str(result.amount),
        "filled": str(result.filled),
        "remaining": str(result.remaining),
        "average": str(result.average) if result.average is not None else None,
        "price": str(result.price) if result.price is not None else None,
        "raw": result.raw,
    }, default=str)


def submit_idempotent(
    session: Session,
    adapter: ExecutionAdapter,
    *,
    strategy_id: int,
    intent: OrderIntent,
    deal_id: str | None = None,
) -> tuple[ExchangeOrderRecord, OrderResult | None]:
    """Persist an order intent before submission and never blindly retry it.

    If a client order id already exists locally, its durable state is returned.
    A missing exchange order id therefore remains a reconciliation problem rather
    than becoming a second exchange submission.
    """
    if not intent.client_order_id:
        raise ExecutionError("Idempotent submission requires client_order_id")

    existing = session.query(ExchangeOrderRecord).filter_by(
        client_order_id=intent.client_order_id
    ).one_or_none()
    if existing is not None:
        if existing.strategy_id != strategy_id or existing.symbol != intent.symbol or existing.side != intent.side:
            raise ExecutionError("Client order id is already bound to a different order intent")
        if existing.status not in {"intent", "unknown"} and existing.exchange_order_id:
            return existing, None
        if existing.status == "unknown":
            raise ExecutionError("Existing order is in unknown state; reconcile before retrying")
        if existing.exchange_order_id:
            return existing, None
    else:
        existing = ExchangeOrderRecord(
            strategy_id=strategy_id,
            deal_id=deal_id,
            symbol=intent.symbol,
            side=intent.side,
            client_order_id=intent.client_order_id,
            status="intent",
            amount=str(intent.amount),
            price=str(intent.price or D("0")),
            filled="0",
            remaining=str(intent.amount),
            raw_json="{}",
        )
        session.add(existing)
        session.flush()
        session.add(AuditEvent(
            event_type="exchange_order_intent_created",
            detail=json.dumps({
                "order_record_id": existing.id,
                "strategy_id": strategy_id,
                "symbol": intent.symbol,
                "side": intent.side,
                "client_order_id": intent.client_order_id,
                "amount": str(intent.amount),
                "price": str(intent.price),
            }),
        ))
        session.commit()

    try:
        result = adapter.place_limit(intent)
    except Exception as exc:
        # The request may have reached the exchange before the connection failed.
        # Mark the intent unknown and require reconciliation. Never auto-submit again.
        existing.status = "unknown"
        existing.raw_json = json.dumps({"submission_error": str(exc)})
        session.add(AuditEvent(
            event_type="exchange_order_submission_unknown",
            detail=json.dumps({
                "order_record_id": existing.id,
                "client_order_id": existing.client_order_id,
                "reason": "submission failed or timed out; reconciliation required",
            }),
        ))
        session.commit()
        raise

    if not result.order_id:
        existing.status = "unknown"
        existing.raw_json = _result_to_json(result)
        session.commit()
        raise ExecutionError("Exchange accepted a response without an order id; reconciliation required")

    existing.exchange_order_id = result.order_id
    existing.status = result.status
    existing.filled = str(result.filled)
    existing.remaining = str(result.remaining)
    existing.average = str(result.average) if result.average is not None else None
    existing.raw_json = _result_to_json(result)
    session.add(AuditEvent(
        event_type="exchange_order_submitted",
        detail=json.dumps({
            "order_record_id": existing.id,
            "client_order_id": existing.client_order_id,
            "exchange_order_id": result.order_id,
            "status": result.status,
        }),
    ))
    session.commit()
    return existing, result
