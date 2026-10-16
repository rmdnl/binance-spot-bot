from dataclasses import dataclass
from decimal import Decimal
D=Decimal

@dataclass(frozen=True)
class Position:
    symbol: str
    base_asset: str
    quote_asset: str
    quantity: D
    cost_quote: D
    realized_quote: D = D("0")
    fees_quote: D = D("0")
    @property
    def average_cost(self): return self.cost_quote/self.quantity if self.quantity>0 else D("0")

def unrealized_pnl(position: Position, mark_price: D) -> D:
    mark=D(str(mark_price))
    return position.quantity*mark-position.cost_quote if position.quantity>0 and mark>0 else D("0")

def realized_pnl_from_sell(position: Position, sell_quantity: D, sell_price: D, fee_quote: D=D("0")) -> D:
    qty=D(str(sell_quantity)); price=D(str(sell_price)); fee=D(str(fee_quote))
    if qty<=0 or qty>position.quantity: raise ValueError("invalid sell quantity")
    return qty*(price-position.average_cost)-fee
