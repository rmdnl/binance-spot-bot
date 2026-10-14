from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

D = Decimal


@dataclass
class Position:
    symbol: str
    base_qty: D = D("0")
    quote_cost: D = D("0")

    @property
    def average_entry(self) -> D | None:
        if self.base_qty <= 0:
            return None
        return self.quote_cost / self.base_qty

    def apply_buy_fill(self, quantity: D, price: D) -> None:
        if quantity <= 0 or price <= 0:
            raise ValueError("fill quantity and price must be positive")
        self.base_qty += quantity
        self.quote_cost += quantity * price

    def apply_sell_fill(self, quantity: D, price: D) -> D:
        if quantity <= 0 or price <= 0:
            raise ValueError("fill quantity and price must be positive")
        if quantity > self.base_qty:
            raise ValueError("sell fill exceeds position quantity")
        avg = self.average_entry
        assert avg is not None
        realized = quantity * (price - avg)
        self.base_qty -= quantity
        self.quote_cost -= quantity * avg
        if self.base_qty == 0:
            self.quote_cost = D("0")
        return realized

    def apply_order_delta(self, side: str, previous_filled: D, current_filled: D, price: D) -> D:
        delta = current_filled - previous_filled
        if delta < 0:
            raise ValueError("exchange filled quantity cannot decrease")
        if delta == 0:
            return D("0")
        if side == "buy":
            self.apply_buy_fill(delta, price)
            return D("0")
        if side == "sell":
            return self.apply_sell_fill(delta, price)
        raise ValueError("unsupported position side")
