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
    stmt = select(SpotOrder).where(
        SpotOrder.environment == environment.value,
        SpotOrder.status.in_([
            OrderStatus.NEW.value,
            OrderStatus.OPEN.value,
            OrderStatus.PARTIALLY_FILLED.value,
        ]),
    )
    if symbol:
        stmt = stmt.where(SpotOrder.symbol == symbol)
    orders = db.scalars(stmt).all()
    adapter = BinanceSpot(environment)
    changed = fills = 0
    for order in orders:
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
    return ReconcileSummary(len(orders), changed, fills)
