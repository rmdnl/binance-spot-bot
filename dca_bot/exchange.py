from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any

import ccxt

from .models import Environment, OrderRequest, OrderType


class ExchangeSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketRules:
    symbol: str
    base: str
    quote: str
    amount_precision: int | None
    price_precision: int | None
    min_amount: D
    min_cost: D


class BinanceSpot:
    """Spot adapter. Order submission is intentionally disabled until safety gates are implemented."""

    def __init__(self, api_key: str, api_secret: str, environment: Environment, *, allow_order_submission: bool = False) -> None:
        if not api_key or not api_secret:
            raise ExchangeSafetyError("Binance API credentials are required")
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise ExchangeSafetyError("Unknown Binance environment")
        self.environment = environment
        self.allow_order_submission = allow_order_submission
        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self.exchange.set_sandbox_mode(environment == Environment.TESTNET)
        self.exchange.load_markets()

    def market(self, symbol: str) -> dict[str, Any]:
        market = self.exchange.market(symbol)
        if market.get("spot") is not True:
            raise ExchangeSafetyError(f"{symbol} is not a Spot market")
        return market

    def market_rules(self, symbol: str) -> MarketRules:
        market = self.market(symbol)
        limits = market.get("limits") or {}
        precision = market.get("precision") or {}
        return MarketRules(
            symbol=symbol,
            base=str(market["base"]),
            quote=str(market["quote"]),
            amount_precision=precision.get("amount") if isinstance(precision.get("amount"), int) else None,
            price_precision=precision.get("price") if isinstance(precision.get("price"), int) else None,
            min_amount=D(str((limits.get("amount") or {}).get("min") or "0")),
            min_cost=D(str((limits.get("cost") or {}).get("min") or "0")),
        )

    def balance(self) -> dict[str, Any]:
        return self.exchange.fetch_balance()

    def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.exchange.fetch_open_orders(symbol)

    def order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        if limit < 5 or limit > 100:
            raise ValueError("order book limit must be between 5 and 100")
        self.market(symbol)
        return self.exchange.fetch_order_book(symbol, limit=limit)

    def ticker(self, symbol: str) -> dict[str, Any]:
        self.market(symbol)
        return self.exchange.fetch_ticker(symbol)

    def create_order(self, request: OrderRequest) -> dict[str, Any]:
        if not self.allow_order_submission:
            raise ExchangeSafetyError("Order submission is disabled until execution safety validation is complete")
        if request.environment != self.environment:
            raise ExchangeSafetyError("Order environment does not match adapter environment")
        if request.amount <= 0:
            raise ExchangeSafetyError("Order amount must be positive")
        if request.order_type != OrderType.MARKET and (request.price is None or request.price <= 0):
            raise ExchangeSafetyError("A positive limit price is required for limit orders")
        raise ExchangeSafetyError("Order submission is not implemented; fail-closed")
