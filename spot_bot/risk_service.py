from __future__ import annotations

from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import RiskState

D = Decimal


def get_risk_state(db: Session) -> RiskState:
    state = db.get(RiskState, 1)
    if state is not None:
        return state

    # Bootstrap the singleton safely when multiple workers start together. The
    # nested transaction confines a duplicate INSERT to a savepoint, allowing
    # the losing worker to re-read the row without poisoning its outer session.
    try:
        with db.begin_nested():
            db.add(RiskState(id=1, halted=False, reason=None))
            db.flush()
    except IntegrityError:
        pass

    state = db.get(RiskState, 1)
    if state is None:
        raise RuntimeError("risk state could not be initialized")
    return state


def lock_risk_state(db: Session) -> RiskState:
    """Acquire the database write boundary shared by halt and order admission.

    The no-op UPDATE takes a row/write lock on databases that support row locking
    and forces SQLite to acquire its write lock. The lock lasts until the caller's
    transaction ends, making the risk decision and subsequent local admission
    atomic with respect to another risk transition.
    """
    state = get_risk_state(db)
    db.execute(update(RiskState).where(RiskState.id == 1).values(id=1))
    db.refresh(state)
    return state


def halt(db: Session, reason: str) -> RiskState:
    state = lock_risk_state(db)
    state.halted = True
    state.reason = reason[:500]
    db.flush()
    return state


def clear_halt(db: Session) -> RiskState:
    state = lock_risk_state(db)
    state.halted = False
    state.reason = None
    db.flush()
    return state


def assert_trading_allowed(db: Session) -> None:
    state = lock_risk_state(db)
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
    return lock_risk_state(db)
