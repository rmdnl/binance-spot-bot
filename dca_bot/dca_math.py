from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


D = Decimal


@dataclass(frozen=True)
class SafetyOrderLevel:
    index: int
    deviation_pct: Decimal
    price: Decimal
    quote_amount: Decimal
    base_quantity: Decimal


@dataclass(frozen=True)
class DcaSummary:
    base_price: Decimal
    base_quote_amount: Decimal
    base_quantity: Decimal
    safety_orders: tuple[SafetyOrderLevel, ...]
    total_quote_required: Decimal
    total_base_quantity: Decimal
    average_entry_price: Decimal
    deepest_deviation_pct: Decimal
    average_entry_deviation_pct: Decimal


def _positive(value: Decimal, name: str) -> Decimal:
    value = D(value)
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def build_dca_summary(
    *,
    base_price: Decimal,
    base_order_quote: Decimal,
    safety_order_quote: Decimal,
    safety_order_count: int,
    initial_deviation_pct: Decimal,
    deviation_scale: Decimal = D("1"),
    safety_volume_scale: Decimal = D("1"),
    averaging_method: str = "fixed_order_value",
    safety_order_base_quantity: Decimal = D("0.001"),
) -> DcaSummary:
    """Calculate a long-only Spot DCA ladder using cumulative arithmetic deviations.

    Deviation scale 1 means evenly spaced orders. A scale above 1 widens the
    distance between successive safety orders, as in the 3Commas arithmetic ladder.
    Amounts are quote-currency values; fees and exchange filters are excluded.
    """
    price0 = _positive(base_price, "base_price")
    base_quote = _positive(base_order_quote, "base_order_quote")
    safety_quote = _positive(safety_order_quote, "safety_order_quote")
    initial = _positive(initial_deviation_pct, "initial_deviation_pct")
    deviation_mult = _positive(deviation_scale, "deviation_scale")
    volume_mult = _positive(safety_volume_scale, "safety_volume_scale")
    base_safety_qty = _positive(safety_order_base_quantity, "safety_order_base_quantity")
    if averaging_method not in {"fixed_order_value", "fixed_coin_amount"}:
        raise ValueError("averaging_method must be fixed_order_value or fixed_coin_amount")
    if not 0 <= safety_order_count <= 30:
        raise ValueError("safety_order_count must be between 0 and 30")
    if initial >= 100:
        raise ValueError("initial_deviation_pct must be below 100")
    if deviation_mult < 1 or volume_mult < 1:
        raise ValueError("deviation_scale and safety_volume_scale must be >= 1")

    base_qty = base_quote / price0
    levels: list[SafetyOrderLevel] = []
    cumulative_deviation = D("0")
    quote_total = base_quote
    qty_total = base_qty
    for index in range(1, safety_order_count + 1):
        step = initial * (deviation_mult ** (index - 1))
        cumulative_deviation += step
        if cumulative_deviation >= 100:
            raise ValueError("Safety order ladder reaches zero or negative price")
        order_price = price0 * (D("1") - cumulative_deviation / D("100"))
        if averaging_method == "fixed_coin_amount":
            order_qty = base_safety_qty * (volume_mult ** (index - 1))
            order_quote = order_qty * order_price
        else:
            order_quote = safety_quote * (volume_mult ** (index - 1))
            order_qty = order_quote / order_price
        levels.append(SafetyOrderLevel(
            index=index,
            deviation_pct=cumulative_deviation,
            price=order_price,
            quote_amount=order_quote,
            base_quantity=order_qty,
        ))
        quote_total += order_quote
        qty_total += order_qty

    avg = quote_total / qty_total
    deepest = levels[-1].deviation_pct if levels else D("0")
    avg_dev = (D("1") - avg / price0) * D("100")
    q = D("0.00000001")
    return DcaSummary(
        base_price=price0,
        base_quote_amount=base_quote,
        base_quantity=base_qty,
        safety_orders=tuple(levels),
        total_quote_required=quote_total.quantize(q, rounding=ROUND_HALF_UP),
        total_base_quantity=qty_total,
        average_entry_price=avg,
        deepest_deviation_pct=deepest,
        average_entry_deviation_pct=avg_dev,
    )
