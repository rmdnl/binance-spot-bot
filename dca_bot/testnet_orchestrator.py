from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from .execution import ExecutionAdapter, OrderIntent, OrderResult
from .models import AuditEvent, ExchangeOrderRecord

D = Decimal


@dataclass(frozen=True)
class SubmitOutcome:
    record: ExchangeOrderRecord
    result: OrderResult | None
    submitted: bool


class TestnetExecutionGateway:
    """Single durable gateway for Binance Spot Testnet order submission."""

    def __init__(self, adapter: ExecutionAdapter, session: Session) -> None:
        self.adapter = adapter
        self.session = session

    def submit_or_recover(self, record: ExchangeOrderRecord) -> SubmitOutcome:
        if record.status in {"closed", "canceled"}:
            return SubmitOutcome(record, None, False)

        existing = self.adapter.fetch_order_by_client_id(record.symbol, record.client_order_id)
        if existing:
            result = existing[0]
            self._persist_result(record, result)
            return SubmitOutcome(record, result, False)

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
        except Exception:
            # Ambiguous submission: never blindly retry. Persist the intent and
            # let the next reconciliation attempt search by client order id.
            record.status = "intent"
            self.session.add(AuditEvent(
                event_type="testnet_order_submit_ambiguous",
                detail=f"order={record.id} client_order_id={record.client_order_id}",
            ))
            self.session.commit()
            raise

        self._persist_result(record, result)
        return SubmitOutcome(record, result, True)

    def _persist_result(self, record: ExchangeOrderRecord, result: OrderResult) -> None:
        record.exchange_order_id = result.order_id or record.exchange_order_id
        record.status = result.status
        record.filled = str(result.filled)
        record.remaining = str(result.remaining)
        record.average = str(result.average) if result.average is not None else None
        record.raw_json = str(result.raw)
        self.session.commit()
