from decimal import Decimal
from typing import Any
import ccxt

from .models import Environment, OrderRequest, OrderStatus, OrderType


class ExchangeSafetyError(RuntimeError):
    pass


class BinanceSpot:
    def __init__(self, api_key: str, api_secret: str, environment: Environment) -> None:
        if not api_key or not api_secret:
            raise ExchangeSafetyError("Binance API credentials are required")
        self.environment = environment
        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self.exchange.set_sandbox_mode(environment == Environment.TESTNET)
        self.exchange.load_markets()

    def market(self, symbol: str) -> dict[str, Any]:
        return self.exchange.market(symbol)

    def balance(self) -> dict[str, Any]:
        return self.exchange.fetch_balance()

    def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.exchange.fetch_open_orders(symbol)

    def create_order(self, request: OrderRequest) -> dict[str, Any]:
        if request.environment != self.environment:
            raise ExchangeSafetyError("Order environment does not match adapter environment")
        params: dict[str, Any] = {}
        if request.client_order_id:
            params["newClientOrderId"] = request.client_order_id
        if request.order_type == OrderType.LIMIT_MAKER:
            params.update({"postOnly": True, "timeInForce": "GTX"})
            order_type = "limit"
        else:
            order_type = request.order_type.value
        try:
            return self.exchange.create_order(
                request.symbol,
                order_type,
                request.side.value,
                float(request.amount),
                float(request.price) if request.price is not None else None,
                params,
            )
        except ccxt.BaseError as exc:
            raise ExchangeSafetyError(f"Binance order failed: {exc}") from exc
