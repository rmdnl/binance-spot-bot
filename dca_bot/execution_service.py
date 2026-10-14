from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal as D

from sqlalchemy.orm import Session

from .execution import ExecutionAdapter, ExecutionError, OrderIntent, OrderResult
from .models import AuditEvent, ExchangeOrderRecord


@dataclass(frozen=True)
class SubmitOutcome:
    record: ExchangeOrderRecord
    result: OrderResult | None
    submitted: bool


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


class ExecutionService:
    """Canonical durable submission/recovery service for Spot Testnet orders."""

    def __init__(self, adapter: ExecutionAdapter, session: Session) -> None:
        self.adapter = adapter
        self.session = session

    def submit_or_recover(self, record: ExchangeOrderRecord) -> SubmitOutcome:
        """Submit an intent once, or recover an already-submitted order by client id.

        The caller owns creation of the ExchangeOrderRecord. This service owns the
        durable exchange submission/recovery transition and never blindly retries
        an ambiguous submission.
        """
        if record.status in {"closed", "canceled"}:
            return SubmitOutcome(record, None, False)

        if record.exchange_order_id and record.status not in {"intent", "unknown"}:
            return SubmitOutcome(record, None, False)

        existing = self.adapter.fetch_order_by_client_id(record.symbol, record.client_order_id)
        if existing:
            result = existing[0]
            self._persist_result(record, result)
            return SubmitOutcome(record, result, False)

        if record.status == "unknown":
            raise ExecutionError("Existing order is in unknown state; reconciliation required")

        intent = OrderIntent(
            symbol=record.symbol,
            side=record.side,  # type: ignore[arg-type]
            amount=D(record.amount),
            price=D(record.price),
            client_order_id=record.client_order_id,
            post_only=True,
        )
        try:
            result = self.adapter.place_limit(intent)
        except Exception as exc:
            record.status = "unknown"
            record.raw_json = json.dumps({"submission_error": str(exc)})
            self.session.add(AuditEvent(
                event_type="exchange_order_submit_ambiguous",
                detail=json.dumps({
                    "order_record_id": record.id,
                    "client_order_id": record.client_order_id,
                    "reason": "submission failed or timed out; reconciliation required",
                }),
            ))
            self.session.commit()
            raise

        if not result.order_id:
            record.status = "unknown"
            record.raw_json = _result_to_json(result)
            self.session.add(AuditEvent(
                event_type="exchange_order_submission_unknown",
                detail=json.dumps({
                    "order_record_id": record.id,
                    "client_order_id": record.client_order_id,
                    "reason": "exchange accepted a response without an order id; reconciliation required",
                }),
            ))
            self.session.commit()
            raise ExecutionError("Exchange accepted a response without an order id; reconciliation required")

        self._persist_result(record, result)
        return SubmitOutcome(record, result, True)

    def _persist_result(self, record: ExchangeOrderRecord, result: OrderResult) -> None:
        record.exchange_order_id = result.order_id or record.exchange_order_id
        record.status = result.status
        record.filled = str(result.filled)
        record.remaining = str(result.remaining)
        record.average = str(result.average) if result.average is not None else None
        record.raw_json = _result_to_json(result)
        self.session.add(AuditEvent(
            event_type="exchange_order_submitted",
            detail=json.dumps({
                "order_record_id": record.id,
                "client_order_id": record.client_order_id,
                "exchange_order_id": result.order_id,
                "status": result.status,
            }),
        ))
        self.session.commit()
