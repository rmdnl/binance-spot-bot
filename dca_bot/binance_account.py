from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any

import ccxt


@dataclass(frozen=True)
class BalanceRow:
    asset: str
    free: D
    used: D
    total: D


@dataclass(frozen=True)
class TradeRow:
    symbol: str
    side: str
    amount: D
    price: D
    cost: D
    fee: D
    fee_currency: str | None
    timestamp: int | None
    order_id: str | None


@dataclass(frozen=True)
class PnlRow:
    symbol: str
    quote: str
    base: str
    realized: D
    unrealized: D
    quantity: D
    cost_basis: D
    mark_price: D


@dataclass(frozen=True)
class QuotePnlSummary:
    """Aggregate PNL only inside one quote-currency bucket."""

    quote: str
    realized: D
    unrealized: D
    total: D


class BinanceAccountData:
    """Read-only Binance Spot account data for the authenticated dashboard."""

    def __init__(self, api_key: str, api_secret: str, *, testnet: bool = True) -> None:
        if not testnet:
            raise ValueError("Dashboard account adapter only permits Binance Spot Testnet")
        if not api_key or not api_secret:
            raise ValueError("Binance API credentials are required")
        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self.exchange.set_sandbox_mode(True)

    @staticmethod
    def _d(value: Any) -> D:
        try:
            result = D(str(value or "0"))
        except Exception:
            return D("0")
        return result if result.is_finite() else D("0")

    def balances(self) -> list[BalanceRow]:
        raw = self.exchange.fetch_balance()
        rows: list[BalanceRow] = []
        total = raw.get("total") or {}
        free = raw.get("free") or {}
        used = raw.get("used") or {}
        for asset, value in total.items():
            total_d = self._d(value)
            free_d = self._d(free.get(asset))
            used_d = self._d(used.get(asset))
            if total_d > 0 or free_d > 0 or used_d > 0:
                rows.append(BalanceRow(str(asset), free_d, used_d, total_d))
        rows.sort(key=lambda row: (-row.total, row.asset))
        return rows

    def recent_trades(self, symbols: list[str], limit_per_symbol: int = 100) -> list[TradeRow]:
        rows: list[TradeRow] = []
        for symbol in symbols:
            try:
                trades = self.exchange.fetch_my_trades(symbol, limit=limit_per_symbol)
            except ccxt.BaseError:
                continue
            for trade in trades:
                fee = trade.get("fee") or {}
                rows.append(TradeRow(
                    symbol=str(trade.get("symbol") or symbol),
                    side=str(trade.get("side") or ""),
                    amount=self._d(trade.get("amount")),
                    price=self._d(trade.get("price")),
                    cost=self._d(trade.get("cost")),
                    fee=self._d(fee.get("cost")),
                    fee_currency=str(fee.get("currency")) if fee.get("currency") else None,
                    timestamp=int(trade["timestamp"]) if trade.get("timestamp") else None,
                    order_id=str(trade.get("order")) if trade.get("order") else None,
                ))
        rows.sort(key=lambda row: row.timestamp or 0, reverse=True)
        return rows[:200]

    def pnl(self, symbols: list[str], limit_per_symbol: int = 1000) -> list[PnlRow]:
        result: list[PnlRow] = []
        markets = self.exchange.load_markets()
        for symbol in symbols:
            market = markets.get(symbol)
            if not market or market.get("spot") is not True:
                continue
            base = str(market["base"])
            quote = str(market["quote"])
            try:
                trades = self.exchange.fetch_my_trades(symbol, limit=limit_per_symbol)
                ticker = self.exchange.fetch_ticker(symbol)
                mark = self._d(ticker.get("last"))
            except ccxt.BaseError:
                continue
            qty = D("0")
            cost_basis = D("0")
            realized = D("0")
            ordered = sorted(trades, key=lambda item: int(item.get("timestamp") or 0))
            for trade in ordered:
                amount = self._d(trade.get("amount"))
                cost = self._d(trade.get("cost"))
                fee = trade.get("fee") or {}
                fee_amount = self._d(fee.get("cost"))
                fee_currency = str(fee.get("currency")) if fee.get("currency") else None
                if amount <= 0 or cost < 0:
                    continue
                if trade.get("side") == "buy":
                    # A base-currency fee reduces the quantity actually held.
                    acquired = amount - fee_amount if fee_currency == base else amount
                    fee_quote = fee_amount if fee_currency == quote else D("0")
                    qty += max(D("0"), acquired)
                    cost_basis += cost + fee_quote
                elif trade.get("side") == "sell":
                    sold = amount
                    if qty > 0:
                        basis = cost_basis * min(sold, qty) / qty
                    else:
                        basis = D("0")
                    # Quote-currency fees reduce realized proceeds. A base fee
                    # is already represented by the executed base quantity.
                    fee_quote = fee_amount if fee_currency == quote else D("0")
                    realized += cost - fee_quote - basis
                    qty = max(D("0"), qty - sold)
                    cost_basis = max(D("0"), cost_basis - basis)
            unrealized = qty * mark - cost_basis
            result.append(PnlRow(
                symbol=symbol,
                quote=quote,
                base=base,
                realized=realized,
                unrealized=unrealized,
                quantity=qty,
                cost_basis=cost_basis,
                mark_price=mark,
            ))
        return result

    @staticmethod
    def summarize_pnl_by_quote(rows: list[PnlRow]) -> list[QuotePnlSummary]:
        """Group PNL by quote asset without converting or mixing currencies.

        BTC/USDT contributes to the USDT bucket, ETH/BTC contributes to the BTC
        bucket, and BTC/USDC contributes to the USDC bucket. There is deliberately
        no fake cross-currency total because a conversion price is required before
        two quote currencies can be meaningfully combined.
        """
        buckets: dict[str, tuple[D, D]] = {}
        for row in rows:
            realized, unrealized = buckets.get(row.quote, (D("0"), D("0")))
            buckets[row.quote] = (realized + row.realized, unrealized + row.unrealized)
        return [
            QuotePnlSummary(
                quote=quote,
                realized=values[0],
                unrealized=values[1],
                total=values[0] + values[1],
            )
            for quote, values in sorted(buckets.items())
        ]
