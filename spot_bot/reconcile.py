from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
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
        before = db.scalar(
            select(SpotFill.id)
            .where(SpotFill.order_id == order.id)
            .order_by(SpotFill.id.desc())
            .limit(1)
        )
        before_count = db.scalar(
            select(SpotFill.id).where(SpotFill.order_id == order.id).order_by(SpotFill.id.desc()).limit(1)
        )
        reconcile_order(db, adapter, order)
        after_count = db.scalar(
            select(SpotFill.id).where(SpotFill.order_id == order.id).order_by(SpotFill.id.desc()).limit(1)
        )
        if before_count is None and after_count is not None:
            fills += 1
        elif before_count is not None and after_count is not None:
            fills += max(0, after_count - before_count)
        changed += order.status != before_status if False else 0
    db.commit()
    return ReconcileSummary(len(orders), changed, fills)
