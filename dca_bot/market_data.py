from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import ccxt


@dataclass(frozen=True)
class SpotTicker:
    symbol: str
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    timestamp_ms: int | None


class BinanceSpotMarketData:
    """Public market-data adapter. It never receives API keys or submits orders."""

    def __init__(self, exchange: Any | None = None) -> None:
        self.exchange = exchange or ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        value = symbol.strip().upper().replace("-", "/")
        if "/" not in value:
            if value.endswith("USDT") and len(value) > 4:
                value = f"{value[:-4]}/USDT"
            else:
                raise ValueError("Use BASE/QUOTE or a supported symbol such as BTCUSDT")
        if value.count("/") != 1 or any(ch.isspace() for ch in value):
            raise ValueError("Invalid trading pair format")
        return value

    def ticker(self, symbol: str) -> SpotTicker:
        normalized = self.normalize_symbol(symbol)
        markets = self.exchange.load_markets()
        market = markets.get(normalized)
        if not market:
            raise ValueError(f"Binance Spot market {normalized} was not found")
        if market.get("spot") is not True or market.get("active") is False:
            raise ValueError(f"{normalized} is not an active Binance Spot market")
        data = self.exchange.fetch_ticker(normalized)
        try:
            last = Decimal(str(data.get("last")))
            bid_raw, ask_raw = data.get("bid"), data.get("ask")
            bid = Decimal(str(bid_raw)) if bid_raw is not None else None
            ask = Decimal(str(ask_raw)) if ask_raw is not None else None
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Exchange returned an invalid ticker price") from exc
        if not last.is_finite() or last <= 0:
            raise ValueError("Exchange returned a non-positive ticker price")
        if bid is not None and (not bid.is_finite() or bid <= 0):
            bid = None
        if ask is not None and (not ask.is_finite() or ask <= 0):
            ask = None
        timestamp = data.get("timestamp")
        return SpotTicker(
            symbol=normalized,
            last=last,
            bid=bid,
            ask=ask,
            timestamp_ms=int(timestamp) if timestamp is not None else None,
        )
