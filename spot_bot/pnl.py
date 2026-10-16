from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InventoryPosition, RealizedPNLEntry, SpotFill

D = Decimal


def quote_asset(symbol: str) -> str:
    parts = symbol.upper().replace("-", "/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid pair: {symbol}")
    return parts[1]


def position_for_symbol(db: Session, symbol: str):
    """Return the persisted inventory ledger state for a symbol.

    InventoryPosition is the single source of truth. This function intentionally
    does not replay SpotFill rows, avoiding a second accounting implementation
    with potentially different fee and inventory semantics.
    """
    position = db.scalar(
        select(InventoryPosition).where(InventoryPosition.symbol == symbol)
    )
    if position is None:
        return D("0"), D("0"), D("0")

    realized = db.scalar(
        select(RealizedPNLEntry.realized_pnl)
        .where(RealizedPNLEntry.symbol == symbol)
    )
    return (
        D(str(position.base_quantity)),
        D(str(position.quote_cost)),
        D(str(realized or D("0"))),
    )


def pnl_snapshot(db: Session, symbol: str, mark_price: D):
    qty, cost, realized = position_for_symbol(db, symbol)
    return {
        "symbol": symbol,
        "quantity": str(qty),
        "cost_basis_quote": str(cost),
        "realized_pnl_quote": str(realized),
        "unrealized_pnl_quote": str(qty * mark_price - cost),
    }
