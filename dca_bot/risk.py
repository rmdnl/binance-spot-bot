from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CapitalEstimate:
    base_order: Decimal
    safety_orders: Decimal
    total: Decimal
    count: int


def estimate_capital(base_order: Decimal, safety_order: Decimal, safety_count: int, volume_scale: Decimal) -> CapitalEstimate:
    """Estimate quote capital for base + all configured safety orders."""
    if base_order <= 0 or safety_order <= 0:
        raise ValueError('Order sizes must be positive')
    if not 0 <= safety_count <= 20:
        raise ValueError('Safety order count must be between 0 and 20')
    if volume_scale < 1:
        raise ValueError('Safety order volume scale must be >= 1')
    sizes = [safety_order * (volume_scale ** i) for i in range(safety_count)]
    safety_total = sum(sizes, Decimal('0'))
    return CapitalEstimate(base_order, safety_total, base_order + safety_total, safety_count)


def estimated_net_profit_pct(target_pct: Decimal, fee_per_side_pct: Decimal, slippage_per_side_pct: Decimal) -> Decimal:
    """A conservative, approximate round-trip estimate. Not a realized PnL calculation."""
    if min(target_pct, fee_per_side_pct, slippage_per_side_pct) < 0:
        raise ValueError('Profit, fee and slippage assumptions cannot be negative')
    return target_pct - Decimal('2') * fee_per_side_pct - Decimal('2') * slippage_per_side_pct


def validate_budget(required_capital: Decimal, per_deal_cap: Decimal, total_cap: Decimal, active_deals: int) -> None:
    if active_deals < 1:
        raise ValueError('At least one active deal slot must be configured')
    if required_capital > per_deal_cap:
        raise ValueError(f'Required capital {required_capital} exceeds per-deal cap {per_deal_cap}')
    if per_deal_cap * active_deals > total_cap:
        raise ValueError('Per-deal cap multiplied by active deals exceeds the total allocation cap')


def reserved_capital_quote(active_deals) -> Decimal:
    """Conservatively reserve each active deal's full configured capital cap.

    Reserving the full cap prevents manual and autonomous entry paths from
    spending the same strategy allocation twice while safety orders remain open.
    """
    total = Decimal("0")
    for deal in active_deals:
        cap = Decimal(str(deal.config.max_capital_per_deal))
        if not cap.is_finite() or cap < 0:
            raise ValueError("Active deal has an invalid capital reservation")
        total += cap
    return total
