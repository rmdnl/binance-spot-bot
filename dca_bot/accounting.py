from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Position:
    asset: str
    quantity: Decimal
    cost_quote: Decimal
    quote: str

    @property
    def average_cost(self) -> Decimal:
        if self.quantity <= 0:
            return Decimal("0")
        return self.cost_quote / self.quantity


@dataclass(frozen=True)
class Pnl:
    realized: Decimal
    unrealized: Decimal
    fees: Decimal

    @property
    def total(self) -> Decimal:
        return self.realized + self.unrealized - self.fees


def unrealized_pnl(position: Position, mark_price: Decimal) -> Decimal:
    if mark_price <= 0 or position.quantity <= 0:
        return Decimal("0")
    return position.quantity * mark_price - position.cost_quote
