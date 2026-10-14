from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .market_data import BinanceSpotMarketData

D = Decimal


@dataclass(frozen=True)
class PairCandidate:
    symbol: str
    quote_volume_24h: D
    spread_pct: D


class AutonomousPairDiscovery:
    """Select Binance Spot USDT candidates from public market data only."""

    def __init__(self, market_data: BinanceSpotMarketData) -> None:
        self.market_data = market_data

    def discover(self, *, min_quote_volume_24h: D = D("5000000"), max_spread_pct: D = D("0.25"), limit: int = 10) -> list[PairCandidate]:
        symbols = self.market_data.discover_usdt_symbols(
            min_quote_volume_24h=float(min_quote_volume_24h),
            max_spread_pct=float(max_spread_pct),
            limit=limit,
        )
        return [PairCandidate(symbol=symbol, quote_volume_24h=D("0"), spread_pct=D("0")) for symbol in symbols]
