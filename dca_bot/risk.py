from dataclasses import dataclass
from decimal import Decimal


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


def check_risk(limits: RiskLimits, snapshot: RiskSnapshot, requested_capital: Decimal) -> None:
    if snapshot.trading_halted:
        raise RuntimeError(f"Trading halted: {snapshot.reason or 'risk lock'}")
    if requested_capital <= 0 or requested_capital > limits.max_capital:
        raise RuntimeError("Requested capital exceeds configured risk limit")
    if snapshot.open_orders >= limits.max_open_orders:
        raise RuntimeError("Maximum open orders reached")
    if snapshot.daily_loss_pct >= limits.max_daily_loss_pct:
        raise RuntimeError("Daily loss limit reached")
    if snapshot.drawdown_pct >= limits.max_drawdown_pct:
        raise RuntimeError("Maximum drawdown reached")
