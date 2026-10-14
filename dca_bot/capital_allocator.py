from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

D = Decimal


@dataclass(frozen=True)
class CapitalAllocation:
    requested_quote: D
    allocated_quote: D
    available_quote: D
    reason: str


class CapitalAllocator:
    """Single source of truth for global and per-deal quote allocation."""

    def __init__(self, global_limit_quote: D, max_per_deal_quote: D) -> None:
        if global_limit_quote <= 0 or max_per_deal_quote <= 0:
            raise ValueError("capital limits must be positive")
        self.global_limit_quote = global_limit_quote
        self.max_per_deal_quote = max_per_deal_quote

    def available(self, allocated_quote: D) -> D:
        if allocated_quote < 0:
            raise ValueError("allocated capital cannot be negative")
        return max(D("0"), self.global_limit_quote - allocated_quote)

    def allocate(self, requested_quote: D, allocated_quote: D) -> CapitalAllocation:
        if requested_quote <= 0:
            return CapitalAllocation(requested_quote, D("0"), self.available(allocated_quote), "invalid_request")
        available = self.available(allocated_quote)
        if available <= 0:
            return CapitalAllocation(requested_quote, D("0"), available, "global_limit_reached")
        amount = min(requested_quote, self.max_per_deal_quote, available)
        reason = "allocated" if amount == requested_quote else "clamped_to_limits"
        return CapitalAllocation(requested_quote, amount, available, reason)
