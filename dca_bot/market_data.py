from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import time
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
        try:
            timestamp_ms = int(timestamp) if timestamp is not None else None
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Exchange returned an invalid ticker timestamp") from exc
        if timestamp_ms is None or timestamp_ms <= 0:
            raise ValueError("Exchange ticker timestamp is missing or invalid")
        age_ms = int(time.time() * 1000) - timestamp_ms
        if age_ms < -60_000:
            raise ValueError("Exchange ticker timestamp is in the future")
        if age_ms > 120_000:
            raise ValueError("Exchange ticker data is stale")
        return SpotTicker(
            symbol=normalized,
            last=last,
            bid=bid,
            ask=ask,
            timestamp_ms=timestamp_ms,
        )


    def candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[list[float]]:
        """Fetch public Binance Spot OHLCV candles; no credentials or orders are used."""
        normalized = self.normalize_symbol(symbol)
        if timeframe not in {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}:
            raise ValueError("Unsupported Binance Spot candle timeframe")
        if limit < 1 or limit > 1000:
            raise ValueError("Candle limit must be between 1 and 1000")
        markets = self.exchange.load_markets()
        market = markets.get(normalized)
        if not market:
            raise ValueError(f"Binance Spot market {normalized} was not found")
        if market.get("spot") is not True or market.get("active") is False:
            raise ValueError(f"{normalized} is not an active Binance Spot market")
        rows = self.exchange.fetch_ohlcv(normalized, timeframe=timeframe, limit=limit)
        if not isinstance(rows, list):
            raise ValueError("Exchange returned invalid candle data")
        return rows
