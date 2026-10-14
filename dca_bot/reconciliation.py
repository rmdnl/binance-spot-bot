from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .execution import ExecutionAdapter, ExecutionError, OrderResult
from .models import AuditEvent, ExchangeOrderRecord, TestnetDealRecord
from .testnet_position_service import sync_order_fill


TERMINAL = {"closed", "canceled"}


def _apply_result(row: ExchangeOrderRecord, result: OrderResult) -> None:
    if result.client_order_id and result.client_order_id != row.client_order_id:
        raise RuntimeError("exchange client order id does not match local order")
    if result.symbol and result.symbol != row.symbol:
        raise RuntimeError("exchange symbol does not match local order")
    if result.side != row.side:
        raise RuntimeError("exchange side does not match local order")
    row.exchange_order_id = result.order_id or row.exchange_order_id
    row.status = result.status
    row.filled = str(result.filled)
    row.remaining = str(result.remaining)
    row.average = str(result.average) if result.average is not None else None
    row.raw_json = json.dumps(result.raw, default=str)


def reconcile_orders(session: Session, adapter: ExecutionAdapter, *, strategy_id: int | None = None, symbol: str | None = None) -> dict[str, Any]:
    query = session.query(ExchangeOrderRecord).filter(~ExchangeOrderRecord.status.in_(TERMINAL))
    if strategy_id is not None:
        query = query.filter_by(strategy_id=strategy_id)
    if symbol is not None:
        query = query.filter_by(symbol=symbol)
    rows = query.all()
    result: dict[str, Any] = {"checked": 0, "updated": 0, "fills_accounted": 0, "adopted_by_client_id": 0, "query_required": [], "halt": [], "errors": []}

    for row in rows:
        result["checked"] += 1
        try:
            if row.exchange_order_id:
                matches = [adapter.fetch_order(row.symbol, row.exchange_order_id)]
            else:
                matches = adapter.fetch_order_by_client_id(row.symbol, row.client_order_id)
                if len(matches) > 1:
                    result["halt"].append({"id": row.id, "client_order_id": row.client_order_id, "reason": "multiple exchange orders match client order id"})
                    continue
                if not matches:
                    result["query_required"].append({"id": row.id, "client_order_id": row.client_order_id, "reason": "no exchange order found; do not auto-resubmit"})
                    continue
                result["adopted_by_client_id"] += 1

            remote = matches[0]
            _apply_result(row, remote)
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
