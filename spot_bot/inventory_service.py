from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import InventoryApplication, InventoryPosition, RealizedPNLEntry, SpotFill

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


def _decimal(value: D | str | float) -> D:
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


def _existing_result(db: Session, fill: SpotFill) -> InventoryResult | None:
    existing = db.scalar(
        select(InventoryApplication).where(InventoryApplication.fill_id == fill.id)
    )
    if existing is None:
        return None
    position = _position(db, fill)
    existing_pnl = db.scalar(
        select(RealizedPNLEntry).where(RealizedPNLEntry.fill_id == fill.id)
    )
    return InventoryResult(
        fill.id,
        False,
        _decimal(position.base_quantity),
        _decimal(position.quote_cost),
        _decimal(existing_pnl.realized_pnl) if existing_pnl else ZERO,
    )


def _reserve_application(db: Session, fill: SpotFill) -> bool:
    """Atomically claim a fill while keeping the marker in the caller transaction."""
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        statement = sqlite_insert(InventoryApplication).values(fill_id=fill.id)
        statement = statement.on_conflict_do_nothing(index_elements=["fill_id"])
        result = db.execute(statement)
        return result.rowcount == 1

    try:
        with db.begin_nested():
            db.add(InventoryApplication(fill_id=fill.id))
            db.flush()
        return True
    except IntegrityError:
        return False


def apply_fill(db: Session, fill: SpotFill) -> InventoryResult:
    """Apply one exchange fill and its realized PNL atomically within caller transaction."""
    amount = _decimal(fill.amount)
    quote_amount = _decimal(fill.quote_amount)
    fee = _decimal(fill.fee)
    if amount <= ZERO or quote_amount < ZERO:
        raise ValueError("fill amount must be positive and quote amount non-negative")
    if fee < ZERO:
        raise ValueError("fill fee cannot be negative")

    existing = _existing_result(db, fill)
    if existing is not None:
        return existing

    if not _reserve_application(db, fill):
        existing = _existing_result(db, fill)
        if existing is None:
            raise RuntimeError("inventory fill claim lost without a visible application")
        return existing

    position = _position(db, fill)
    _, quote = _assets(fill.symbol)
    fee_currency = (fill.fee_currency or "").upper()
    base_asset = position.base_asset.upper()
    realized = ZERO

    if fill.side.lower() == "buy":
        base_received = amount - fee if fee_currency == base_asset else amount
        if base_received < ZERO:
            raise ValueError("base-asset fee exceeds bought amount")
        cost = quote_amount + fee if fee_currency == quote else quote_amount
        position.base_quantity = _decimal(position.base_quantity) + base_received
        position.quote_cost = _decimal(position.quote_cost) + cost
    elif fill.side.lower() == "sell":
        base_sold = amount + fee if fee_currency == base_asset else amount
        current_qty = _decimal(position.base_quantity)
        current_cost = _decimal(position.quote_cost)
        if base_sold > current_qty:
            raise ValueError("sell fill exceeds available inventory")
        released_cost = ZERO if current_qty == ZERO else _decimal(current_cost * base_sold / current_qty)
        proceeds = _decimal(quote_amount - fee) if fee_currency == quote else quote_amount
        if proceeds < ZERO:
            raise ValueError("quote-asset fee exceeds sell proceeds")
        position.base_quantity = _decimal(current_qty - base_sold)
        position.quote_cost = _decimal(current_cost - released_cost)
        realized = _decimal(proceeds - released_cost)
        db.add(RealizedPNLEntry(
            fill_id=fill.id,
            environment=fill.order.environment,
            symbol=fill.symbol,
            quantity=base_sold,
            entry_cost=released_cost,
            proceeds=proceeds,
            fees=fee if fee_currency == quote else ZERO,
            realized_pnl=realized,
            timestamp=fill.timestamp,
        ))
    else:
        raise ValueError(f"unsupported fill side: {fill.side}")

    if position.base_quantity < ZERO or position.quote_cost < ZERO:
        raise ValueError("inventory invariant violated")
    position.updated_at = fill.timestamp
    db.flush()
    return InventoryResult(fill.id, True, _decimal(position.base_quantity), _decimal(position.quote_cost), realized)
