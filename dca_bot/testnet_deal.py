from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from .execution import ExecutionAdapter, ExecutionError, OrderIntent, OrderResult
from .models import AuditEvent, ExchangeOrderRecord


@dataclass(frozen=True)
class TestnetDealIntent:
    strategy_id: int
    symbol: str
    side: str
    amount: Decimal
    price: Decimal
    client_order_id: str


class TestnetDealService:
    """Small, fail-closed deal lifecycle built on the durable order layer."""

    def __init__(self, adapter: ExecutionAdapter) -> None:
        self.adapter = adapter

    def create_entry_intent(self, session: Session, intent: TestnetDealIntent) -> ExchangeOrderRecord:
        existing = session.query(ExchangeOrderRecord).filter_by(
            client_order_id=intent.client_order_id
        ).one_or_none()
        if existing is not None:
            if existing.symbol != intent.symbol or existing.side != intent.side:
                raise RuntimeError("Client order id collision with different order identity")
            return existing

        row = ExchangeOrderRecord(
            strategy_id=intent.strategy_id,
            deal_id=None,
            symbol=intent.symbol,
            side=intent.side,
            client_order_id=intent.client_order_id,
            status="intent",
            amount=str(intent.amount),
            price=str(intent.price),
            filled="0",
            remaining=str(intent.amount),
            raw_json="{}",
        )
        session.add(row)
        session.flush()
        session.add(AuditEvent(
            event_type="testnet_entry_intent_created",
            detail=f"strategy={intent.strategy_id} symbol={intent.symbol} client_order_id={intent.client_order_id}",
        ))
        session.commit()
        return row

    def submit_entry(self, session: Session, row: ExchangeOrderRecord) -> OrderResult:
        if row.status != "intent" or row.exchange_order_id:
            raise RuntimeError("Only a fresh durable intent may be submitted")
        try:
            result = self.adapter.place_limit(OrderIntent(
                symbol=row.symbol,
                side=row.side,  # type: ignore[arg-type]
                amount=Decimal(row.amount),
                price=Decimal(row.price),
                client_order_id=row.client_order_id,
                post_only=True,
            ))
        except ExecutionError as exc:
            row.status = "unknown"
            row.raw_json = '{"error":"ambiguous_submission"}'
            session.add(AuditEvent(
                event_type="testnet_order_submission_unknown",
                detail=f"order_id={row.id} client_order_id={row.client_order_id}",
            ))
            session.commit()
            raise

        row.exchange_order_id = result.order_id or None
        row.status = result.status
        row.filled = str(result.filled)
        row.remaining = str(result.remaining)
        row.average = str(result.average) if result.average is not None else None
        session.add(AuditEvent(
            event_type="testnet_order_submitted",
            detail=f"order_id={row.id} exchange_order_id={row.exchange_order_id} status={row.status}",
        ))
        session.commit()
        return result

    def reconcile_entry(self, session: Session, row: ExchangeOrderRecord) -> dict[str, Any]:
        if row.status in {"closed", "canceled"}:
            return {"status": row.status, "changed": False}
        try:
            if row.exchange_order_id:
                result = self.adapter.fetch_order(row.symbol, row.exchange_order_id)
            else:
                matches = self.adapter.fetch_order_by_client_id(row.symbol, row.client_order_id)
                if len(matches) != 1:
                    if len(matches) > 1:
                        row.status = "halt"
                        session.add(AuditEvent(event_type="testnet_reconciliation_halt", detail=f"order_id={row.id} duplicate client_order_id"))
                        session.commit()
                        return {"status": "halt", "reason": "duplicate exchange orders"}
                    return {"status": "query_required", "changed": False}
                result = matches[0]

            if result.symbol != row.symbol or result.client_order_id not in {None, row.client_order_id}:
                row.status = "halt"
                session.add(AuditEvent(event_type="testnet_reconciliation_halt", detail=f"order_id={row.id} identity mismatch"))
                session.commit()
                return {"status": "halt", "reason": "exchange identity mismatch"}

            row.exchange_order_id = result.order_id or row.exchange_order_id
            row.status = result.status
            row.filled = str(result.filled)
            row.remaining = str(result.remaining)
            row.average = str(result.average) if result.average is not None else None
            session.add(AuditEvent(event_type="testnet_order_reconciled", detail=f"order_id={row.id} status={row.status} filled={row.filled}"))
            session.commit()
            return {"status": row.status, "changed": True, "filled": row.filled}
        except ExecutionError:
            return {"status": "query_required", "changed": False}
