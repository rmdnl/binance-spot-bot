from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

D = Decimal


@dataclass(frozen=True)
class RiskLimits:
    max_capital: Decimal
    max_open_orders: int = 20
    max_daily_loss_pct: Decimal = Decimal("2")
    max_drawdown_pct: Decimal = Decimal("5")
    range_break_buffer_pct: Decimal = Decimal("1")


@dataclass(frozen=True)
class RiskSnapshot:
    equity: Decimal
    available: Decimal
    daily_loss_pct: Decimal
    drawdown_pct: Decimal
    open_orders: int
    trading_halted: bool
    reason: str | None = None


def quote_asset(symbol: str) -> str:
    parts = symbol.upper().replace("-", "/").split("/")
    if len(parts) == 2 and all(parts):
        return parts[1]
    # CCXT compact symbols are not reliably separable without market metadata.
    raise ValueError(f"Pair must use BASE/QUOTE format: {symbol!r}")


def reserved_capital_by_quote(deals: Iterable[object]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for deal in deals:
        symbol = str(getattr(deal, "symbol", ""))
        quote = quote_asset(symbol)
        state = getattr(deal, "state_json", None)
        amount = getattr(deal, "reserved_quote", None)
        if amount is None and isinstance(state, dict):
            amount = state.get("quote_spent", "0")
        if amount is None:
            amount = "0"
        value = D(str(amount))
        if not value.is_finite() or value < 0:
            raise ValueError("reserved capital must be a finite non-negative Decimal")
        totals[quote] = totals.get(quote, D("0")) + value
    return totals


def reserved_capital_quote(deals: Iterable[object]) -> Decimal:
    # Compatibility helper: only valid when every deal uses the same quote asset.
    buckets = reserved_capital_by_quote(deals)
    if len(buckets) > 1:
        raise ValueError("Cannot sum reservations across different quote currencies")
    return next(iter(buckets.values()), D("0"))


def check_risk(limits: RiskLimits, snapshot: RiskSnapshot, requested_capital: Decimal) -> None:
    if snapshot.trading_halted:
        raise RuntimeError(f"Trading halted: {snapshot.reason or 'risk lock'}")
    if requested_capital <= 0 or requested_capital > limits.max_capital:
        raise RuntimeError("Requested capital exceeds configured risk limit")
    if requested_capital > snapshot.available:
        raise RuntimeError("Insufficient available quote balance")
    if snapshot.open_orders >= limits.max_open_orders:
        raise RuntimeError("Maximum open orders reached")
    if snapshot.daily_loss_pct >= limits.max_daily_loss_pct:
        raise RuntimeError("Daily loss limit reached")
    if snapshot.drawdown_pct >= limits.max_drawdown_pct:
        raise RuntimeError("Maximum drawdown reached")
