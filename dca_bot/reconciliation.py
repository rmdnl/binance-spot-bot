from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from .execution import ExecutionAdapter, ExecutionError, OrderResult
from .models import AuditEvent, ExchangeOrderRecord, TestnetDealRecord
from .order_state import OrderSnapshot, apply_exchange_result
from .testnet_position_service import sync_order_fill

D = Decimal
TERMINAL = {"filled", "canceled"}


def _snapshot(row: ExchangeOrderRecord) -> OrderSnapshot:
    return OrderSnapshot(
        client_order_id=row.client_order_id,
        symbol=row.symbol,
        side=row.side,
        requested_amount=D(row.amount),
        requested_price=D(row.price),
        exchange_order_id=row.exchange_order_id,
        filled_amount=D(row.filled),
        average_price=D(row.average) if row.average else None,
    )


def _apply_state_machine(row: ExchangeOrderRecord, result: OrderResult) -> None:
    """Apply an exchange observation through the canonical order state machine."""
    updated = apply_exchange_result(_snapshot(row), result)
    row.exchange_order_id = updated.exchange_order_id
    row.status = updated.status.value
    row.filled = str(updated.filled_amount)
    row.remaining = str(max(D(row.amount) - updated.filled_amount, D("0")))
    row.average = str(updated.average_price) if updated.average_price is not None else None
    row.raw_json = json.dumps(result.raw, default=str)


def reconcile_orders(session: Session, adapter: ExecutionAdapter, *, strategy_id: int | None = None, symbol: str | None = None) -> dict[str, Any]:
    query = session.query(ExchangeOrderRecord).filter(~ExchangeOrderRecord.status.in_(TERMINAL))
    if strategy_id is not None:
        query = query.filter_by(strategy_id=strategy_id)
    if symbol is not None:
        query = query.filter_by(symbol=symbol)
    rows = query.order_by(ExchangeOrderRecord.id.asc()).all()
    result: dict[str, Any] = {"checked": 0, "updated": 0, "fills_accounted": 0, "adopted_by_client_id": 0, "query_required": [], "halt": [], "errors": []}

    for row in rows:
        result["checked"] += 1
        try:
            if row.exchange_order_id:
                remote = adapter.fetch_order(row.symbol, row.exchange_order_id)
            else:
                matches = adapter.fetch_order_by_client_id(row.symbol, row.client_order_id)
                if len(matches) > 1:
                    result["halt"].append({"id": row.id, "client_order_id": row.client_order_id, "reason": "multiple exchange orders match client order id"})
                    continue
                if not matches:
                    result["query_required"].append({"id": row.id, "client_order_id": row.client_order_id, "reason": "no exchange order found; do not auto-resubmit"})
                    continue
                remote = matches[0]
                result["adopted_by_client_id"] += 1

            previous_filled = D(row.filled)
            _apply_state_machine(row, remote)
            result["updated"] += 1

            if row.deal_id is not None:
                deal = session.get(TestnetDealRecord, row.deal_id)
                if deal is None:
                    result["halt"].append({"id": row.id, "reason": "local deal record missing"})
                    session.rollback()
                    continue
                delta = sync_order_fill(session, deal, row)
                if delta:
                    result["fills_accounted"] += 1
            else:
                session.commit()

            if previous_filled != D(row.filled):
                session.add(AuditEvent(event_type="exchange_order_reconciled", detail=json.dumps({"order_record_id": row.id, "client_order_id": row.client_order_id, "exchange_order_id": row.exchange_order_id, "status": row.status, "filled": row.filled})))
                session.commit()
        except (ExecutionError, RuntimeError, ValueError) as exc:
            session.rollback()
            result["errors"].append({"id": row.id, "client_order_id": row.client_order_id, "error": str(exc)})

    return result


def reconcile_open_orders(session: Session, adapter: ExecutionAdapter, *, strategy_id: int | None = None, symbol: str | None = None) -> dict[str, Any]:
    local_query = session.query(ExchangeOrderRecord).filter(~ExchangeOrderRecord.status.in_(TERMINAL))
    if strategy_id is not None:
        local_query = local_query.filter_by(strategy_id=strategy_id)
    if symbol is not None:
        local_query = local_query.filter_by(symbol=symbol)
    local_rows = local_query.all()
    symbols = {row.symbol for row in local_rows}
    if symbol:
        symbols.add(symbol)

    remote: list[OrderResult] = []
    errors: list[dict[str, str]] = []
    for item in sorted(symbols):
        try:
            remote.extend(adapter.fetch_open_orders(item))
        except ExecutionError as exc:
            errors.append({"symbol": item, "error": str(exc)})

    by_client: dict[str, list[OrderResult]] = {}
    for order in remote:
        if order.client_order_id:
            by_client.setdefault(order.client_order_id, []).append(order)

    local_clients = {row.client_order_id for row in local_rows}
    result: dict[str, Any] = {"local": len(local_rows), "remote_open": len(remote), "orphan_remote": [], "duplicate_remote": [], "errors": errors, "halt": []}
    for client_id, matches in by_client.items():
        if len(matches) > 1:
            result["duplicate_remote"].append(client_id)
            result["halt"].append({"client_order_id": client_id, "reason": "multiple exchange open orders share one client order id"})
        elif client_id not in local_clients:
            result["orphan_remote"].append(client_id)
            result["halt"].append({"client_order_id": client_id, "reason": "exchange open order has no local durable record"})

    if result["halt"]:
        session.add(AuditEvent(event_type="exchange_reconciliation_halt", detail=json.dumps(result, default=str)))
        session.commit()
    return result
