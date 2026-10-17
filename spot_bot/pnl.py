from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Environment, InventoryPosition, RealizedPNLEntry

D = Decimal


def quote_asset(symbol: str) -> str:
    parts = symbol.upper().replace("-", "/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid pair: {symbol}")
    return parts[1]


def position_for_symbol(db: Session, symbol: str, environment: Environment = Environment.TESTNET):
    """Return persisted inventory and realized PNL for one environment/symbol.

    InventoryPosition and RealizedPNLEntry are the accounting source of truth.
    SpotFill rows are intentionally not replayed here, avoiding a second
    accounting implementation with different fee and inventory semantics.
    """
    position = db.scalar(
        select(InventoryPosition).where(
            InventoryPosition.environment == environment,
            InventoryPosition.symbol == symbol,
        )
    )
    if position is None:
        return D("0"), D("0"), D("0")

    realized = db.scalar(
        select(func.coalesce(func.sum(RealizedPNLEntry.realized_pnl), D("0"))).where(
            RealizedPNLEntry.environment == environment,
            RealizedPNLEntry.symbol == symbol,
        )
    )
    return (
        D(str(position.base_quantity)),
        D(str(position.quote_cost)),
        D(str(realized or D("0"))),
    )


def pnl_snapshot(
    db: Session,
    symbol: str,
    mark_price: D,
    environment: Environment = Environment.TESTNET,
):
    qty, cost, realized = position_for_symbol(db, symbol, environment)
    return {
        "symbol": symbol,
        "environment": environment.value,
        "quantity": str(qty),
        "cost_basis_quote": str(cost),
        "realized_pnl_quote": str(realized),
        "unrealized_pnl_quote": str(qty * mark_price - cost),
    }
