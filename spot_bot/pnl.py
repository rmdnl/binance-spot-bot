from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import OrderSide, SpotFill

D = Decimal

def quote_asset(symbol: str) -> str:
    parts = symbol.upper().replace("-", "/").split("/")
    if len(parts) != 2 or not all(parts): raise ValueError(f"Invalid pair: {symbol}")
    return parts[1]

def position_for_symbol(db: Session, symbol: str):
    fills = db.scalars(select(SpotFill).where(SpotFill.symbol == symbol).order_by(SpotFill.timestamp, SpotFill.id)).all()
    qty = cost = realized = D("0")
    quote = quote_asset(symbol)
    for f in fills:
        fee_quote = f.fee if f.fee_currency == quote else D("0")
        if f.side == OrderSide.BUY.value:
            qty += f.amount; cost += f.quote_amount + fee_quote
        else:
            if qty <= 0: continue
            sell_qty = min(f.amount, qty)
            unit_cost = cost / qty
            proceeds = f.quote_amount - fee_quote
            realized += proceeds * sell_qty / f.amount - unit_cost * sell_qty
            qty -= sell_qty; cost -= unit_cost * sell_qty
    return qty, cost, realized

def pnl_snapshot(db: Session, symbol: str, mark_price: D):
    qty, cost, realized = position_for_symbol(db, symbol)
    return {"symbol": symbol, "quantity": str(qty), "cost_basis_quote": str(cost), "realized_pnl_quote": str(realized), "unrealized_pnl_quote": str(qty * mark_price - cost)}
