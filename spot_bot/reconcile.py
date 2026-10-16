from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .exchange import BinanceSpot
from .models import Environment, OrderStatus, SpotOrder
from .order_service import reconcile_order, record_fills

D = Decimal


@dataclass(frozen=True)
class ReconcileSummary:
    checked: int
    changed: int
    fills_recorded: int


def reconcile_open_orders(db: Session, environment: Environment, symbol: str | None = None) -> ReconcileSummary:
    stmt = select(SpotOrder).where(SpotOrder.environment == environment.value, SpotOrder.status.in_([OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value]))
    if symbol:
        stmt = stmt.where(SpotOrder.symbol == symbol)
    orders = db.scalars(stmt).all()
    adapter = BinanceSpot(environment)
    changed = fills = 0
    for order in orders:
        before = order.status
        reconcile_order(db, adapter, order)
        fills += record_fills(db, adapter, order)
        changed += order.status != before
    db.commit()
    return ReconcileSummary(len(orders), changed, fills)
