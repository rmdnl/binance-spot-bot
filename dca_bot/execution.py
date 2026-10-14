from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Literal, Protocol

import ccxt


D = Decimal
OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["open", "closed", "canceled", "unknown"]


class ExecutionError(RuntimeError):
    """Base execution-layer error."""


class ExecutionSafetyError(ExecutionError):
    """Raised when execution is not explicitly safe to perform."""


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    base: str
    quote: str
    amount_step: Decimal
    price_step: Decimal
    min_amount: Decimal
    min_cost: Decimal


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    amount: Decimal
    price: Decimal | None = None
    client_order_id: str | None = None
    post_only: bool = True


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    client_order_id: str | None
    symbol: str
    side: OrderSide
    status: OrderStatus
    amount: Decimal
    filled: Decimal
    remaining: Decimal
    average: Decimal | None
    price: Decimal | None
    raw: dict[str, Any]


class ExecutionAdapter(Protocol):
    def place_limit(self, intent: OrderIntent) -> OrderResult: ...
    def fetch_order(self, symbol: str, order_id: str) -> OrderResult: ...
    def fetch_open_orders(self, symbol: str | None = None) -> list[OrderResult]: ...
    def cancel_order(self, symbol: str, order_id: str) -> OrderResult: ...


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return D(default)
    result = D(str(value))
    if not result.is_finite():
        raise ExecutionError("Exchange returned a non-finite numeric value")
    return result


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if value <= 0 or step <= 0:
        raise ValueError("value and step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _status(value: Any) -> OrderStatus:
    normalized = str(value or "unknown").lower()
    if normalized in {"open", "closed", "canceled"}:
        return normalized  # type: ignore[return-value]
    return "unknown"


class BinanceSpotTestnetAdapter:
    """Conservative CCXT adapter for Binance Spot Testnet only.

    This adapter deliberately refuses live Binance execution. It is an execution
    primitive, not a strategy engine: callers must perform risk checks before
    invoking place_limit().
    """

    def __init__(self, api_key: str, api_secret: str, *, testnet: bool = True) -> None:
        if not testnet:
            raise ExecutionSafetyError("Binance adapter only permits Spot Testnet")
        if not api_key or not api_secret:
            raise ExecutionSafetyError("Binance Testnet API credentials are required")
        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self.exchange.set_sandbox_mode(True)
        self.exchange.load_markets()

    def _market(self, symbol: str) -> dict[str, Any]:
        try:
            return self.exchange.market(symbol)
        except Exception as exc:  # pragma: no cover - CCXT-specific failure
            raise ExecutionError(f"Unable to load Binance market {symbol}: {exc}") from exc

    def symbol_rules(self, symbol: str) -> SymbolRules:
        market = self._market(symbol)
        precision = market.get("precision") or {}
        limits = market.get("limits") or {}
        amount_step = _decimal(precision.get("amount"))
        price_step = _decimal(precision.get("price"))

        # CCXT may expose decimal precision as an integer number of places.
        if amount_step >= 1 and isinstance(precision.get("amount"), int):
            amount_step = D("1") / (D("10") ** int(precision["amount"]))
        if price_step >= 1 and isinstance(precision.get("price"), int):
            price_step = D("1") / (D("10") ** int(precision["price"]))

        if amount_step <= 0 or price_step <= 0:
            raise ExecutionError(f"Incomplete Binance precision metadata for {symbol}")

        return SymbolRules(
            symbol=symbol,
            base=str(market["base"]),
            quote=str(market["quote"]),
            amount_step=amount_step,
            price_step=price_step,
            min_amount=_decimal((limits.get("amount") or {}).get("min")),
            min_cost=_decimal((limits.get("cost") or {}).get("min")),
        )

    def normalize_limit(self, intent: OrderIntent) -> OrderIntent:
        if intent.side not in {"buy", "sell"}:
            raise ValueError("Unsupported order side")
        if intent.amount <= 0:
            raise ValueError("Order amount must be positive")
        if intent.price is None or intent.price <= 0:
            raise ValueError("Limit order price must be positive")

        rules = self.symbol_rules(intent.symbol)
        amount = _floor_step(intent.amount, rules.amount_step)
        price = _floor_step(intent.price, rules.price_step)
        if amount < rules.min_amount:
            raise ExecutionSafetyError("Order amount is below Binance min amount")
        if amount * price < rules.min_cost:
            raise ExecutionSafetyError("Order notional is below Binance min cost")

        return OrderIntent(
            symbol=intent.symbol,
            side=intent.side,
            amount=amount,
            price=price,
            client_order_id=intent.client_order_id,
            post_only=intent.post_only,
        )

    @staticmethod
    def _result(raw: dict[str, Any]) -> OrderResult:
        info = raw.get("info") or {}
        client_id = raw.get("clientOrderId") or info.get("clientOrderId")
        return OrderResult(
            order_id=str(raw.get("id") or info.get("orderId") or ""),
            client_order_id=str(client_id) if client_id else None,
            symbol=str(raw.get("symbol") or ""),
            side=str(raw.get("side") or "buy"),  # type: ignore[arg-type]
            status=_status(raw.get("status")),
            amount=_decimal(raw.get("amount")),
            filled=_decimal(raw.get("filled")),
            remaining=_decimal(raw.get("remaining")),
            average=_decimal(raw.get("average"), "0") if raw.get("average") is not None else None,
            price=_decimal(raw.get("price"), "0") if raw.get("price") is not None else None,
            raw=raw,
        )

    def place_limit(self, intent: OrderIntent) -> OrderResult:
        normalized = self.normalize_limit(intent)
        params: dict[str, Any] = {}
        if normalized.client_order_id:
            params["newClientOrderId"] = normalized.client_order_id
        if normalized.post_only:
            params["postOnly"] = True
            params["timeInForce"] = "GTX"

        try:
            raw = self.exchange.create_order(
                normalized.symbol,
                "limit",
                normalized.side,
                float(normalized.amount),
                float(normalized.price),
                params,
            )
        except ccxt.BaseError as exc:
            raise ExecutionError(f"Binance order submission failed: {exc}") from exc
        return self._result(raw)

    def fetch_order(self, symbol: str, order_id: str) -> OrderResult:
        try:
            return self._result(self.exchange.fetch_order(order_id, symbol))
        except ccxt.BaseError as exc:
            raise ExecutionError(f"Binance order lookup failed: {exc}") from exc

    def fetch_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        try:
            return [self._result(item) for item in self.exchange.fetch_open_orders(symbol)]
        except ccxt.BaseError as exc:
            raise ExecutionError(f"Binance open-order reconciliation failed: {exc}") from exc

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        try:
            return self._result(self.exchange.cancel_order(order_id, symbol))
        except ccxt.BaseError as exc:
            raise ExecutionError(f"Binance cancel failed: {exc}") from exc
