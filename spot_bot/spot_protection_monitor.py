from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import SessionLocal
from .exchange import BinanceSpot
from .models import Environment, OrderRequest, OrderSide, OrderStatus, OrderType, SpotOrder, SpotProtection
from .order_service import create_intent, recover_or_submit, reconcile_order, record_fills

log = logging.getLogger(__name__)
D = Decimal
ACTIVE = {OrderStatus.NEW.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}
TERMINAL = {OrderStatus.FILLED.value, OrderStatus.CANCELED.value, OrderStatus.EXPIRED.value}


def monitor_spot_protections_once(session_factory=SessionLocal, adapter_factory=BinanceSpot) -> None:
    db: Session = session_factory()
    try:
        rows = db.scalars(select(SpotProtection).where(
            SpotProtection.status.in_(["waiting_entry", "armed", "exiting"])
        ).order_by(SpotProtection.id)).all()
        adapters = {}
        for protection in rows:
            try:
                env = Environment(protection.environment)
                adapter = adapters[env.value] if env.value in adapters else adapters.setdefault(env.value, adapter_factory(env))
                entry = db.get(SpotOrder, protection.entry_order_id)
                if entry is None:
                    protection.status = "error"
                    protection.last_error = "Entry order record missing"
                    db.commit()
                    continue

                if protection.status == "waiting_entry":
                    if entry.status in ACTIVE:
                        reconcile_order(db, adapter, entry)
                        record_fills(db, adapter, entry)
                        db.commit()
                    if entry.status not in (TERMINAL | {OrderStatus.REJECTED.value}):
                        continue
                    qty = D(str(entry.filled_amount or "0"))
                    if qty <= 0:
                        protection.status = "closed"
                        protection.last_error = "Entry ended without a fill; no position to protect"
                        db.commit()
                        continue
                    protection.protected_quantity = qty
                    protection.status = "armed"
                    protection.last_error = None
                    db.commit()

                if protection.status == "armed":
                    ticker = adapter.ticker(protection.symbol)
                    raw = ticker.get("last") or ticker.get("close")
                    price = D(str(raw or "0"))
                    if not price.is_finite() or price <= 0:
                        raise RuntimeError("Ticker returned no usable positive price")
                    protection.last_price = price
                    reason = None
                    if protection.stop_loss_price is not None and price <= D(str(protection.stop_loss_price)):
                        reason = "stop_loss"
                    elif protection.take_profit_price is not None and price >= D(str(protection.take_profit_price)):
                        reason = "take_profit"
                    if reason is None:
                        db.commit()
                        continue

                    # Persist the unique exit intent and EXITING state before network I/O.
                    result = db.execute(
                        update(SpotProtection)
                        .where(SpotProtection.id == protection.id, SpotProtection.status == "armed")
                        .values(status="exiting", trigger_reason=reason, last_error=None)
                    )
                    if result.rowcount != 1:
                        db.rollback()
                        continue
                    protection.status = "exiting"
                    exit_order = create_intent(db, OrderRequest(
                        env, protection.symbol, OrderSide.SELL, OrderType.MARKET,
                        protection.protected_quantity,
                    ), risk_reducing=True)
                    protection.exit_order_id = exit_order.id
                    db.commit()

                if protection.status == "exiting" and protection.exit_order_id is not None:
                    exit_order = db.get(SpotOrder, protection.exit_order_id)
                    if exit_order is None:
                        protection.status = "error"
                        protection.last_error = "Persisted protective exit order is missing"
                        db.commit()
                        continue
                    if exit_order.status in {OrderStatus.NEW.value, OrderStatus.REJECTED.value}:
                        recover_or_submit(db, adapter, exit_order, risk_reducing=True)
                        db.commit()
                    elif exit_order.status in ACTIVE:
                        reconcile_order(db, adapter, exit_order)
                        record_fills(db, adapter, exit_order)
                        db.commit()
                    if exit_order.status == OrderStatus.FILLED.value:
                        protection.status = "closed"
                        protection.last_error = None
                        db.commit()
            except Exception as exc:
                db.rollback()
                current = db.get(SpotProtection, protection.id)
                if current is not None:
                    current.last_error = str(exc)[:500]
                    db.commit()
                log.exception("Manual Spot protection monitor failed for protection %s", protection.id)
    finally:
        db.close()
