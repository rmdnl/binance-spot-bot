from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InventoryApplication, InventoryPosition, SpotFill

D = Decimal
ZERO = D("0")
ACCOUNTING_QUANTUM = D("0.000000000001")


@dataclass(frozen=True)
class InventoryResult:
    fill_id: int
    applied: bool
    base_quantity: D
    quote_cost: D
    realized_pnl: D = ZERO


def _decimal(value: D | str | int | float) -> D:
    """Normalize DB numeric values before accounting arithmetic.

    SQLite may round SQLAlchemy Numeric values through a binary float. Twelve
    decimal places are retained, which is finer than the quote precision used
    by Binance Spot while preventing binary-float residue from leaking into
    accounting results.
    """
    return D(str(value)).quantize(ACCOUNTING_QUANTUM, rounding=ROUND_HALF_UP)


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
    environment = fill.order.environment
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

    The caller owns the transaction. All inventory changes and the application
    marker are committed or rolled back together.
    """
    amount = _decimal(fill.amount)
    quote_amount = _decimal(fill.quote_amount)
    fee = _decimal(fill.fee)
    if amount <= ZERO or quote_amount < ZERO:
        raise ValueError("fill amount must be positive and quote amount non-negative")
    if fee < ZERO:
        raise ValueError("fill fee cannot be negative")

    already_applied = db.scalar(
        select(InventoryApplication).where(InventoryApplication.fill_id == fill.id)
    )
    position = _position(db, fill)
    if already_applied is not None:
        return InventoryResult(
            fill.id,
            False,
            _decimal(position.base_quantity),
            _decimal(position.quote_cost),
        )

    # Insert the durable idempotency marker before mutating inventory. If any
    # later validation fails, the caller's transaction rollback removes it too.
    db.add(InventoryApplication(fill_id=fill.id))
    db.flush()

    _, quote = _assets(fill.symbol)
    fee_currency = (fill.fee_currency or "").upper()
    base_asset = position.base_asset.upper()

    if fill.side.lower() == "buy":
        base_received = amount - fee if fee_currency == base_asset else amount
        if base_received < ZERO:
            raise ValueError("base-asset fee exceeds bought amount")
        cost = quote_amount + fee if fee_currency == quote else quote_amount
        position.base_quantity = _decimal(position.base_quantity) + base_received
        position.quote_cost = _decimal(position.quote_cost) + cost
        realized = ZERO
    elif fill.side.lower() == "sell":
        base_sold = amount + fee if fee_currency == base_asset else amount
        current_qty = _decimal(position.base_quantity)
        current_cost = _decimal(position.quote_cost)
        if base_sold > current_qty:
            raise ValueError("sell fill exceeds available inventory")
        released_cost = ZERO if current_qty == ZERO else _decimal(current_cost * base_sold / current_qty)
        proceeds = quote_amount - fee if fee_currency == quote else quote_amount
        if proceeds < ZERO:
            raise ValueError("quote-asset fee exceeds sell proceeds")
        position.base_quantity = _decimal(current_qty - base_sold)
        position.quote_cost = _decimal(current_cost - released_cost)
        realized = _decimal(proceeds - released_cost)
    else:
        raise ValueError(f"unsupported fill side: {fill.side}")

    if position.base_quantity < ZERO or position.quote_cost < ZERO:
        raise ValueError("inventory invariant violated")

    position.updated_at = fill.timestamp
    db.flush()
    return InventoryResult(
        fill.id,
        True,
        _decimal(position.base_quantity),
        _decimal(position.quote_cost),
        realized,
    )
