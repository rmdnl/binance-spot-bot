from dataclasses import dataclass
from decimal import Decimal
D=Decimal

@dataclass(frozen=True)
class RiskLimits:
    max_capital_quote: D
    max_open_orders: int=20
    max_daily_loss_pct: D=D("2")
    max_drawdown_pct: D=D("5")
    range_break_buffer_pct: D=D("1")

@dataclass(frozen=True)
class RiskSnapshot:
    equity_quote: D
    available_quote: D
    daily_realized_pnl_quote: D
    drawdown_pct: D
    open_orders: int
    halted: bool=False
    reason: str|None=None

def quote_asset(symbol: str)->str:
    parts=symbol.upper().replace("-","/").split("/")
    if len(parts)!=2 or not all(parts): raise ValueError("Pair must be BASE/QUOTE")
    return parts[1]

def check_order_risk(limits: RiskLimits, snapshot: RiskSnapshot, capital: D):
    if snapshot.halted: raise RuntimeError(f"Trading halted: {snapshot.reason or 'risk lock'}")
    if capital<=0 or capital>limits.max_capital_quote: raise RuntimeError("Capital exceeds risk limit")
    if capital>snapshot.available_quote: raise RuntimeError("Insufficient available quote balance")
    if snapshot.open_orders>=limits.max_open_orders: raise RuntimeError("Maximum open orders reached")
    if snapshot.daily_realized_pnl_quote < 0 and abs(snapshot.daily_realized_pnl_quote) >= snapshot.equity_quote*limits.max_daily_loss_pct/D(100): raise RuntimeError("Daily loss limit reached")
    if snapshot.drawdown_pct>=limits.max_drawdown_pct: raise RuntimeError("Maximum drawdown reached")
