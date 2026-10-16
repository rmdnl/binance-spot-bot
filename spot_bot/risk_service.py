from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from .models import RiskState

D = Decimal


def get_risk_state(db: Session) -> RiskState:
    state = db.get(RiskState, 1)
    if state is None:
        state = RiskState(id=1, halted=False, reason=None)
        db.add(state)
        db.flush()
    return state


def halt(db: Session, reason: str) -> RiskState:
    state = get_risk_state(db)
    state.halted = True
    state.reason = reason[:500]
    db.flush()
    return state


def clear_halt(db: Session) -> RiskState:
    state = get_risk_state(db)
    state.halted = False
    state.reason = None
    db.flush()
    return state


def assert_trading_allowed(db: Session) -> None:
    state = get_risk_state(db)
    if state.halted:
        raise RuntimeError(f"risk halt active: {state.reason or 'unspecified'}")


def evaluate_drawdown(db: Session, equity: D, peak_equity: D, max_drawdown: D) -> RiskState:
    if equity <= 0 or peak_equity <= 0:
        raise ValueError("equity and peak_equity must be positive")
    if max_drawdown < 0:
        raise ValueError("max_drawdown must be non-negative")
    drawdown = (peak_equity - equity) / peak_equity
    if drawdown >= max_drawdown:
        return halt(db, f"equity drawdown {drawdown:.4%} >= max {max_drawdown:.4%}")
    return get_risk_state(db)
