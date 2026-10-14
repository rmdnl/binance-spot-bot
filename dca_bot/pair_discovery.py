from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .market_data import BinanceSpotMarketData

D = Decimal


@dataclass(frozen=True)
class PairCandidate:
    symbol: str
    quote_volume_24h: D
    spread_pct: D
    last: D
    bid: D
    ask: D
    score: D


class AutonomousPairDiscovery:
    """Select Binance Spot USDT candidates using execution-quality data."""

    def __init__(self, market_data: BinanceSpotMarketData) -> None:
        self.market_data = market_data

    def discover(self, *, min_quote_volume_24h: D = D("5000000"), max_spread_pct: D = D("0.25"), limit: int = 10) -> list[PairCandidate]:
        if limit <= 0:
            return []
        markets = self.market_data.exchange.load_markets()
        tickers = self.market_data.exchange.fetch_tickers()
        allowed = set(self.market_data.discover_usdt_symbols(
            min_quote_volume_24h=float(min_quote_volume_24h),
            max_spread_pct=float(max_spread_pct),
            limit=limit,
        ))
        candidates: list[PairCandidate] = []
        for symbol in allowed:
            if symbol not in markets:
                continue
            ticker: Any = tickers.get(symbol)
            if not ticker:
                continue
            try:
                last = D(str(ticker["last"]))
                bid = D(str(ticker["bid"]))
                ask = D(str(ticker["ask"]))
                volume = D(str(ticker.get("quoteVolume") or (D(str(ticker["baseVolume"])) * last)))
            except (KeyError, TypeError, ValueError):
                continue
            if min(last, bid, ask, volume) <= 0 or ask < bid:
                continue
            midpoint = (ask + bid) / D("2")
            spread = (ask - bid) / midpoint * D("100")
            if spread > max_spread_pct:
                continue
            liquidity_score = min(D("50"), volume / min_quote_volume_24h * D("50"))
            spread_score = max(D("0"), D("50") * (D("1") - spread / max_spread_pct)) if max_spread_pct > 0 else D("0")
            score = liquidity_score + spread_score
            candidates.append(PairCandidate(symbol, volume, spread, last, bid, ask, score))
        candidates.sort(key=lambda c: (-c.score, -c.quote_volume_24h, c.symbol))
        return candidates[:limit]
