from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import time
from math import isfinite
from typing import Any

import ccxt

from .dca_math import build_dca_summary
from .strategy import StrategyConfig


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
        self._discovery_ticker_snapshot: dict[str, Any] | None = None
        self._discovery_ticker_snapshot_at = 0.0

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
        if market.get("spot") is not True or market.get("active") is not True:
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


    def validate_minimum_order_sizes(
        self, symbol: str, config: StrategyConfig, price: Decimal | str | float
    ) -> None:
        """Reject a simulated DCA ladder if any planned order violates market minimums.

        This checks Binance market minimum amount and minimum quote cost from CCXT
        metadata. It does not replace precision/step-size validation for a future
        execution adapter; exchange execution remains disabled.
        """
        normalized = self.normalize_symbol(symbol)
        markets = self.exchange.load_markets()
        market = markets.get(normalized)
        if not market:
            raise ValueError(f"Binance Spot market {normalized} was not found")
        if market.get("spot") is not True or market.get("active") is not True:
            raise ValueError(f"{normalized} is not an active Binance Spot market")

        price0 = Decimal(str(price))
        if not price0.is_finite() or price0 <= 0:
            raise ValueError("Reference price must be positive and finite")
        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        min_amount_raw = amount_limits.get("min")
        min_cost_raw = cost_limits.get("min")
        if min_amount_raw is None or min_cost_raw is None:
            raise ValueError(f"{normalized} minimum-order metadata is unavailable; entry blocked")
        min_amount = Decimal(str(min_amount_raw))
        min_cost = Decimal(str(min_cost_raw))
        if (not min_amount.is_finite() or min_amount < 0
                or not min_cost.is_finite() or min_cost < 0):
            raise ValueError(f"{normalized} has invalid Binance minimum-order metadata")

        info = market.get("info") or {}
        filters = info.get("filters") or []
        filter_map = {
            str(item.get("filterType")): item
            for item in filters
            if isinstance(item, dict) and item.get("filterType")
        }
        lot_filter = filter_map.get("LOT_SIZE")
        price_filter = filter_map.get("PRICE_FILTER")
        if not isinstance(lot_filter, dict) or not isinstance(price_filter, dict):
            raise ValueError(f"{normalized} Binance LOT_SIZE/PRICE_FILTER metadata is unavailable; entry blocked")
        try:
            amount_step = Decimal(str(lot_filter["stepSize"]))
            filter_min_amount = Decimal(str(lot_filter["minQty"]))
            filter_max_amount = Decimal(str(lot_filter["maxQty"]))
            price_tick = Decimal(str(price_filter["tickSize"]))
            filter_min_price = Decimal(str(price_filter["minPrice"]))
            filter_max_price = Decimal(str(price_filter["maxPrice"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{normalized} Binance precision metadata is invalid; entry blocked") from exc
        metadata_values = (
            amount_step, filter_min_amount, filter_max_amount,
            price_tick, filter_min_price, filter_max_price,
        )
        if any(not value.is_finite() or value < 0 for value in metadata_values):
            raise ValueError(f"{normalized} Binance precision metadata is invalid; entry blocked")
        if amount_step <= 0 or price_tick <= 0 or filter_max_amount <= 0 or filter_max_price <= 0:
            raise ValueError(f"{normalized} Binance precision metadata is invalid; entry blocked")
        min_amount = max(min_amount, filter_min_amount)

        def floor_step(value: Decimal, step: Decimal) -> Decimal:
            return (value // step) * step

        summary = build_dca_summary(
            base_price=price0,
            base_order_quote=Decimal(str(config.base_order_quote)),
            safety_order_quote=Decimal(str(config.safety_order_quote)),
            safety_order_count=config.max_safety_orders,
            initial_deviation_pct=Decimal(str(config.initial_deviation_pct)),
            deviation_scale=Decimal(str(config.deviation_scale)),
            safety_volume_scale=Decimal(str(config.safety_order_volume_scale)),
            averaging_method=config.averaging_method,
            safety_order_base_quantity=Decimal(str(config.safety_order_base_quantity)),
            dca_mode=config.dca_mode,
        )
        planned_orders = [("base order", price0, summary.base_quote_amount, summary.base_quantity)]
        planned_orders.extend(
            (f"safety order {level.index}", level.price, level.quote_amount, level.base_quantity)
            for level in summary.safety_orders
        )
        for label, order_price, quote_amount, base_quantity in planned_orders:
            rounded_price = floor_step(order_price, price_tick)
            rounded_quantity = floor_step(base_quantity, amount_step)
            if rounded_price <= 0 or rounded_price < filter_min_price or rounded_price > filter_max_price:
                raise ValueError(
                    f"{normalized} {label} price cannot satisfy Binance PRICE_FILTER"
                )
            if rounded_quantity < min_amount:
                raise ValueError(
                    f"{normalized} {label} quantity after LOT_SIZE step rounding "
                    f"({rounded_quantity}) is below the market minimum amount {min_amount}"
                )
            if rounded_quantity > filter_max_amount:
                raise ValueError(
                    f"{normalized} {label} quantity after LOT_SIZE step rounding "
                    f"({rounded_quantity}) exceeds Binance maximum amount {filter_max_amount}"
                )
            rounded_cost = rounded_price * rounded_quantity
            if rounded_cost < min_cost:
                raise ValueError(
                    f"{normalized} {label} quote value after price/quantity rounding "
                    f"({rounded_cost} USDT) is below the market minimum cost {min_cost} USDT"
                )


    def discover_usdt_symbols(
        self,
        *,
        min_quote_volume_24h: float = 5_000_000,
        max_spread_pct: float = 0.25,
        limit: int = 10,
    ) -> list[str]:
        """Return liquid, fresh, active Binance Spot BASE/USDT markets, ranked by quote volume.

        This uses public bulk ticker data once per discovery pass. It does not use
        credentials or submit orders. Missing/stale tickers and leveraged tokens
        are rejected rather than guessed into the candidate universe.
        """
        if (
            not isfinite(min_quote_volume_24h)
            or not isfinite(max_spread_pct)
            or min_quote_volume_24h < 0
            or max_spread_pct <= 0
            or not 1 <= limit <= 100
        ):
            raise ValueError("Invalid automatic pair discovery limits")
        markets = self.exchange.load_markets()
        # A bulk 24h ticker request can be expensive on Binance. Reuse the same
        # public snapshot across profiles within a scan while enforcing a short TTL.
        now_monotonic = time.monotonic()
        if (
            self._discovery_ticker_snapshot is None
            or now_monotonic - self._discovery_ticker_snapshot_at > 15
        ):
            tickers = self.exchange.fetch_tickers()
            if not isinstance(tickers, dict):
                raise ValueError("Exchange returned invalid bulk ticker data")
            self._discovery_ticker_snapshot = tickers
            self._discovery_ticker_snapshot_at = now_monotonic
        tickers = self._discovery_ticker_snapshot
        now_ms = int(time.time() * 1000)
        ranked: list[tuple[float, str]] = []
        leveraged_suffixes = ("DOWN", "BULL", "BEAR", "UP", "2L", "2S", "3L", "3S", "5L", "5S")
        # A suffix alone is not enough to identify a leveraged token: legitimate
        # assets can end in UP (for example GROUP). Treat a symbol as a likely
        # leveraged token only when removing its suffix matches another active
        # Spot USDT base asset in the loaded market universe.
        active_usdt_bases = {
            str(market["base"]).upper()
            for market in markets.values()
            if market.get("spot") is True
            and market.get("active") is True
            and market.get("quote") == "USDT"
            and market.get("base")
            and str(market["base"]).upper() != "USDT"
        }

        def is_likely_leveraged_base(base: str) -> bool:
            for suffix in leveraged_suffixes:
                underlying = base[:-len(suffix)] if base.endswith(suffix) else ""
                if underlying and underlying in active_usdt_bases:
                    return True
            return False

        for symbol, market in markets.items():
            if (
                market.get("spot") is not True
                or market.get("active") is not True
                or market.get("quote") != "USDT"
                or not market.get("base")
                or market.get("base") == "USDT"
            ):
                continue
            base = str(market["base"]).upper()
            if is_likely_leveraged_base(base):
                continue
            ticker = tickers.get(symbol)
            if not isinstance(ticker, dict):
                continue
            try:
                last = float(ticker.get("last"))
                bid = float(ticker.get("bid"))
                ask = float(ticker.get("ask"))
                timestamp = ticker.get("timestamp")
                quote_volume = ticker.get("quoteVolume")
                if quote_volume is None:
                    base_volume = float(ticker.get("baseVolume"))
                    quote_volume = base_volume * last
                quote_volume = float(quote_volume)
                timestamp_ms = int(timestamp) if timestamp is not None else 0
            except (TypeError, ValueError, OverflowError):
                continue
            if (
                not all(map(lambda value: value > 0 and value < float("inf"), (last, bid, ask, quote_volume)))
                or ask < bid
                or timestamp_ms <= 0
                or timestamp_ms > now_ms + 60_000
                or now_ms - timestamp_ms > 180_000
                or quote_volume < min_quote_volume_24h
            ):
                continue
            midpoint = (ask + bid) / 2
            spread_pct = ((ask - bid) / midpoint) * 100
            if spread_pct > max_spread_pct:
                continue
            normalized = self.normalize_symbol(symbol)
            ranked.append((quote_volume, normalized))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [symbol for _, symbol in ranked[:limit]]

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
        if market.get("spot") is not True or market.get("active") is not True:
            raise ValueError(f"{normalized} is not an active Binance Spot market")
        rows = self.exchange.fetch_ohlcv(normalized, timeframe=timeframe, limit=limit)
        if not isinstance(rows, list):
            raise ValueError("Exchange returned invalid candle data")
        return rows
