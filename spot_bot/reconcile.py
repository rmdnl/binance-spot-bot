from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .exchange import BinanceSpot
from .models import Environment, OrderStatus, SpotFill, SpotOrder
from .order_service import reconcile_order


@dataclass(frozen=True)
class ReconcileSummary:
    checked: int
    changed: int
    fills_recorded: int


def reconcile_open_orders(db: Session, environment: Environment, symbol: str | None = None) -> ReconcileSummary:
    active_stmt = select(SpotOrder).where(
        SpotOrder.environment == environment.value,
        SpotOrder.status.in_([
            OrderStatus.NEW.value,
            OrderStatus.OPEN.value,
            OrderStatus.PARTIALLY_FILLED.value,
        ]),
    )
    if symbol:
        active_stmt = active_stmt.where(SpotOrder.symbol == symbol)
    active_orders = db.scalars(active_stmt).all()

    # Terminal status can arrive before the exchange exposes its final trades.
    # Revisit only terminal orders whose reported filled amount exceeds the
    # amount of fills persisted locally, avoiding a broad terminal-order scan.
    persisted_amount = (
        select(func.coalesce(func.sum(SpotFill.amount), 0))
        .where(SpotFill.order_id == SpotOrder.id)
        .correlate(SpotOrder)
        .scalar_subquery()
    )
    terminal_stmt = select(SpotOrder).where(
        SpotOrder.environment == environment.value,
        SpotOrder.status.in_([
            OrderStatus.FILLED.value,
            OrderStatus.CANCELED.value,
            OrderStatus.EXPIRED.value,
        ]),
        SpotOrder.exchange_order_id.is_not(None),
        SpotOrder.filled_amount > persisted_amount,
    )
    if symbol:
        terminal_stmt = terminal_stmt.where(SpotOrder.symbol == symbol)
    terminal_orders = db.scalars(terminal_stmt).all()

    adapter = BinanceSpot(environment)
    changed = fills = 0
    for order in [*active_orders, *terminal_orders]:
        before_status = order.status
        before_count = db.scalar(
            select(func.count()).select_from(SpotFill).where(SpotFill.order_id == order.id)
        )
        reconcile_order(db, adapter, order)
        after_count = db.scalar(
            select(func.count()).select_from(SpotFill).where(SpotFill.order_id == order.id)
        )
        fills += max(0, after_count - before_count)
        changed += order.status != before_status
    db.commit()
    return ReconcileSummary(len(active_orders) + len(terminal_orders), changed, fills)
