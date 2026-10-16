from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InventoryPosition, RealizedPNLEntry, SpotFill

D = Decimal
ZERO = D("0")
QUANTUM = D("0.000000000001")


@dataclass(frozen=True)
class RealizedPNLResult:
    fill_id: int
    recorded: bool
    quantity: D
    entry_cost: D
    proceeds: D
    fees: D
    realized_pnl: D


def _d(value: D | str | float) -> D:
    return D(str(value)).quantize(QUANTUM, rounding=ROUND_HALF_UP)


def record_realized_pnl(db: Session, fill: SpotFill, *, released_cost: D | None = None) -> RealizedPNLResult:
    """Record realized PNL for a SELL fill exactly once.

    The caller owns the transaction. The ledger row and any inventory mutation
    therefore commit or roll back together.
    """
    if fill.side.lower() != "sell":
        raise ValueError("realized PNL can only be recorded for sell fills")

    existing = db.scalar(select(RealizedPNLEntry).where(RealizedPNLEntry.fill_id == fill.id))
    if existing is not None:
        return RealizedPNLResult(
            fill.id,
            False,
            _d(existing.quantity),
            _d(existing.entry_cost),
            _d(existing.proceeds),
            _d(existing.fees),
            _d(existing.realized_pnl),
        )

    position = db.scalar(
        select(InventoryPosition).where(
            InventoryPosition.environment == fill.order.environment,
            InventoryPosition.symbol == fill.symbol,
        )
    )
    if position is None:
        raise ValueError("inventory position not found")

    quantity = _d(fill.amount)
    current_qty = _d(position.base_quantity)
    current_cost = _d(position.quote_cost)
    if quantity <= ZERO or quantity > current_qty:
        raise ValueError("sell fill exceeds available inventory")

    if released_cost is None:
        released_cost = _d(current_cost * quantity / current_qty) if current_qty else ZERO
    else:
        released_cost = _d(released_cost)

    _, quote = fill.symbol.upper().split("/", 1)
    fee = _d(fill.fee)
    fee_quote = fee if (fill.fee_currency or "").upper() == quote else ZERO
    proceeds = _d(fill.quote_amount) - fee_quote
    if proceeds < ZERO:
        raise ValueError("quote-asset fee exceeds sell proceeds")

    realized = _d(proceeds - released_cost)
    db.add(
        RealizedPNLEntry(
            fill_id=fill.id,
            environment=fill.order.environment,
            symbol=fill.symbol,
            quantity=quantity,
            entry_cost=released_cost,
            proceeds=proceeds,
            fees=fee_quote,
            realized_pnl=realized,
            timestamp=fill.timestamp,
        )
    )
    db.flush()
    return RealizedPNLResult(
        fill.id, True, quantity, released_cost, proceeds, fee_quote, realized
    )


def unrealized_pnl(position: InventoryPosition, market_price: D) -> D:
    """Return mark-to-market PNL for the currently open inventory."""
    price = _d(market_price)
    quantity = _d(position.base_quantity)
    cost = _d(position.quote_cost)
    if price < ZERO:
        raise ValueError("market price cannot be negative")
    return _d(quantity * price - cost)
