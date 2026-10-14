from __future__ import annotations

from decimal import Decimal

D = Decimal


def required_exit_price(entry_price: D, target_net_pct: D, buy_fee_pct: D, sell_fee_pct: D, slippage_pct: D) -> D:
    """Return the minimum exit price required to achieve target net return.

    Fees and slippage are modeled conservatively as percentages of notional.
    """
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if target_net_pct < 0 or buy_fee_pct < 0 or sell_fee_pct < 0 or slippage_pct < 0:
        raise ValueError("profit and cost percentages must be non-negative")

    # Net proceeds = exit * (1 - sell fee - slippage).
    # Total cost = entry * (1 + buy fee).
    denominator = D("1") - sell_fee_pct - slippage_pct
    if denominator <= 0:
        raise ValueError("sell fee plus slippage leaves no executable proceeds")
    return entry_price * (D("1") + buy_fee_pct) * (D("1") + target_net_pct) / denominator
