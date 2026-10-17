from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

D = Decimal


@dataclass(frozen=True)
class GridEconomics:
    buy_price: D
    sell_price: D
    amount: D
    gross_quote: D
    buy_fee_quote: D
    sell_fee_quote: D
    slippage_quote: D
    net_quote: D
    gross_pct: D
    net_pct: D


def calculate_grid_economics(
    buy_price: D,
    sell_price: D,
    amount: D,
    maker_fee_pct: D,
    slippage_pct: D,
) -> GridEconomics:
    buy = D(str(buy_price))
    sell = D(str(sell_price))
    qty = D(str(amount))
    fee = D(str(maker_fee_pct)) / D("100")
    slip = D(str(slippage_pct)) / D("100")
    if buy <= 0 or sell <= buy or qty <= 0:
        raise ValueError("invalid grid economics inputs")
    if fee < 0 or slip < 0:
        raise ValueError("fee and slippage cannot be negative")
    buy_quote = buy * qty
    sell_quote = sell * qty
    gross = sell_quote - buy_quote
    buy_fee = buy_quote * fee
    sell_fee = sell_quote * fee
    slippage = (buy_quote + sell_quote) * slip
    net = gross - buy_fee - sell_fee - slippage
    gross_pct = gross / buy_quote * D("100")
    net_pct = net / buy_quote * D("100")
    return GridEconomics(buy, sell, qty, gross, buy_fee, sell_fee, slippage, net, gross_pct, net_pct)


def enforce_min_net_profit(economics: GridEconomics, minimum_net_profit_pct: D) -> None:
    minimum = D(str(minimum_net_profit_pct))
    if minimum < 0:
        raise ValueError("minimum net profit cannot be negative")
    if economics.net_pct < minimum:
        raise ValueError(
            f"grid net profit {economics.net_pct:.8f}% is below minimum {minimum:.8f}%"
        )
