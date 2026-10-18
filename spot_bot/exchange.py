from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import ccxt

from .config import get_settings
from .models import Environment, OrderRequest, OrderType

D = Decimal


class ExchangeSafetyError(RuntimeError):
    pass


class BinanceSpot:
    def __init__(self, environment: Environment):
        self.environment = environment
        settings = get_settings()
        key, secret = settings.credentials(environment.value)
        if not key or not secret:
            raise ExchangeSafetyError(f"Binance {environment.value} credentials are missing")
        self.exchange = ccxt.binance({
            "apiKey": key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self.exchange.set_sandbox_mode(environment == Environment.TESTNET)
        self.exchange.load_markets()

    def market(self, symbol: str) -> dict[str, Any]:
        market = self.exchange.market(symbol.upper())
        if market.get("spot") is not True:
            raise ExchangeSafetyError(f"{symbol} is not a Spot market")
        return market

    def validate_order(self, request: OrderRequest) -> None:
        if request.environment != self.environment:
            raise ExchangeSafetyError("Environment mismatch")
        if request.amount <= 0:
            raise ExchangeSafetyError("Amount must be positive")
        if request.order_type != OrderType.MARKET and (request.price is None or request.price <= 0):
            raise ExchangeSafetyError("Positive limit price required")
        market = self.market(request.symbol)
        amount = D(self.exchange.amount_to_precision(request.symbol, request.amount))
        if amount != request.amount:
            raise ExchangeSafetyError(f"Amount violates Binance precision: {request.amount} -> {amount}")
        minimum = D(str((market.get("limits") or {}).get("amount", {}).get("min") or "0"))
        if minimum and amount < minimum:
            raise ExchangeSafetyError(f"Amount {amount} is below Binance minimum {minimum}")
        price = None
        if request.price is not None:
            price = D(self.exchange.price_to_precision(request.symbol, request.price))
            if price != request.price:
                raise ExchangeSafetyError(f"Price violates Binance precision: {request.price} -> {price}")
        cost = amount * (price or D("0"))
        minimum_cost = D(str((market.get("limits") or {}).get("cost", {}).get("min") or "0"))
        if request.order_type != OrderType.MARKET and minimum_cost and cost < minimum_cost:
            raise ExchangeSafetyError(f"Order notional {cost} is below Binance minimum {minimum_cost}")
        filters = market.get("info", {}).get("filters", [])
        for item in filters:
            if item.get("filterType") in {"NOTIONAL", "MIN_NOTIONAL"} and item.get("minNotional"):
                if request.order_type != OrderType.MARKET and cost < D(str(item["minNotional"])):
                    raise ExchangeSafetyError(f"Order notional violates Binance {item['filterType']} filter")

    def balance(self):
        return self.exchange.fetch_balance()

    def ticker(self, symbol: str):
        self.market(symbol)
        return self.exchange.fetch_ticker(symbol)

    def open_orders(self, symbol: str | None = None):
        return self.exchange.fetch_open_orders(symbol)

    def recent_trades(self, symbol: str | None = None, limit: int = 50):
        return self.exchange.fetch_my_trades(symbol, limit=limit)

    def trades_for_order(self, symbol: str, exchange_order_id: str):
        """Fetch fills for one Binance order instead of relying on the latest account trades."""
        try:
            order_id = int(exchange_order_id)
        except (TypeError, ValueError):
            return self.recent_trades(symbol, limit=100)
        return self.exchange.fetch_my_trades(
            symbol,
            limit=1000,
            params={"orderId": order_id},
        )

    def historical_price(self, symbol: str, timestamp: datetime, *, max_age_ms: int = 120_000) -> D | None:
        """Return latest public trade price at/before fill time, refusing stale history."""
        self.market(symbol)
        target_ms = int(timestamp.timestamp() * 1000)
        trades = self.exchange.fetch_trades(symbol, since=max(0, target_ms - max_age_ms), limit=1000)
        eligible = []
        for trade in trades or []:
            trade_ms = trade.get("timestamp")
            raw_price = trade.get("price")
            if trade_ms is None or raw_price is None:
                continue
            price = D(str(raw_price))
            if price.is_finite() and price > 0 and target_ms - max_age_ms <= int(trade_ms) <= target_ms:
                eligible.append((int(trade_ms), price))
        return max(eligible, key=lambda item: item[0])[1] if eligible else None

    def fetch_order(self, exchange_order_id: str, symbol: str):
        return self.exchange.fetch_order(exchange_order_id, symbol)

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str):
        raw = self.exchange.private_get_order({
            "symbol": self.exchange.market_id(symbol),
            "origClientOrderId": client_order_id,
            "recvWindow": 10000,
        })
        return self.exchange.parse_order(raw, self.exchange.market(symbol))

    def cancel_order(self, exchange_order_id: str, symbol: str):
        return self.exchange.cancel_order(exchange_order_id, symbol)

    def create_order(self, request: OrderRequest):
        settings = get_settings()
        if not settings.allow_order_submission:
            raise ExchangeSafetyError("Order submission is disabled")
        self.validate_order(request)
        params: dict[str, Any] = {"newClientOrderId": request.client_order_id} if request.client_order_id else {}
        if request.post_only or request.order_type == OrderType.LIMIT_MAKER:
            params["postOnly"] = True
        order_type = "limit" if request.order_type == OrderType.LIMIT_MAKER else request.order_type.value
        return self.exchange.create_order(
            request.symbol,
            order_type,
            request.side.value,
            float(request.amount),
            float(request.price) if request.price is not None else None,
            params,
        )
