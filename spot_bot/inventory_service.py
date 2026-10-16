from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InventoryPosition, SpotFill

D = Decimal
ZERO = D("0")


@dataclass(frozen=True)
class InventoryResult:
    fill_id: int
    applied: bool
    base_quantity: D
    quote_cost: D
    realized_pnl: D = ZERO


def _assets(symbol: str) -> tuple[str, str]:
    try:
        base, quote = symbol.upper().split("/", 1)
    except ValueError as exc:
        raise ValueError(f"invalid spot symbol: {symbol}") from exc
    if not base or not quote:
        raise ValueError(f"invalid spot symbol: {symbol}")
    return base, quote


def _position(db: Session, fill: SpotFill) -> InventoryPosition:
    base, quote = _assets(fill.symbol)
    environment = db.get(SpotFill, fill.id).order.environment
    position = db.scalar(
        select(InventoryPosition).where(
            InventoryPosition.environment == environment,
            InventoryPosition.symbol == fill.symbol,
        )
    )
    if position is None:
        position = InventoryPosition(
            environment=environment,
            symbol=fill.symbol,
            base_asset=base,
            quote_asset=quote,
            base_quantity=ZERO,
            quote_cost=ZERO,
        )
        db.add(position)
        db.flush()
    return position


def apply_fill(db: Session, fill: SpotFill) -> InventoryResult:
    """Apply one exchange fill to persistent inventory exactly once.

    The caller owns the transaction. If this function raises, the caller must
    roll back so neither the inventory row nor the fill application is partial.
    """
    if fill.amount <= 0 or fill.quote_amount < 0:
        raise ValueError("fill amount must be positive and quote amount non-negative")
    if fill.fee < 0:
        raise ValueError("fill fee cannot be negative")

    # The fill itself is the idempotency key. A committed fill is never applied twice.
    if db.info.get("inventory_applied", set()) and fill.id in db.info["inventory_applied"]:
        position = _position(db, fill)
        return InventoryResult(fill.id, False, D(str(position.base_quantity)), D(str(position.quote_cost)))

    position = _position(db, fill)
    amount = D(str(fill.amount))
    quote_amount = D(str(fill.quote_amount))
    fee = D(str(fill.fee))
    _, quote = _assets(fill.symbol)
    fee_currency = (fill.fee_currency or "").upper()

    if fill.side.lower() == "buy":
        base_received = amount - fee if fee_currency == position.base_asset.upper() else amount
        if base_received < 0:
            raise ValueError("base-asset fee exceeds bought amount")
        cost = quote_amount + fee if fee_currency == quote else quote_amount
        position.base_quantity = D(str(position.base_quantity)) + base_received
        position.quote_cost = D(str(position.quote_cost)) + cost
        realized = ZERO
    elif fill.side.lower() == "sell":
        base_sold = amount + fee if fee_currency == position.base_asset.upper() else amount
        current_qty = D(str(position.base_quantity))
        current_cost = D(str(position.quote_cost))
        if base_sold > current_qty:
            raise ValueError("sell fill exceeds available inventory")
        released_cost = ZERO if current_qty == ZERO else current_cost * base_sold / current_qty
        proceeds = quote_amount - fee if fee_currency == quote else quote_amount
        position.base_quantity = current_qty - base_sold
        position.quote_cost = current_cost - released_cost
        realized = proceeds - released_cost
    else:
        raise ValueError(f"unsupported fill side: {fill.side}")

    if position.base_quantity < ZERO or position.quote_cost < ZERO:
        raise ValueError("inventory invariant violated")

    position.updated_at = fill.timestamp
    db.flush()
    db.info.setdefault("inventory_applied", set()).add(fill.id)
    return InventoryResult(
        fill.id,
        True,
        D(str(position.base_quantity)),
        D(str(position.quote_cost)),
        realized,
    )
