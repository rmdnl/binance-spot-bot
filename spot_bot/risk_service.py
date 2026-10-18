from __future__ import annotations

from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .dashboard_metrics import dashboard_metrics
from .models import EquityHighWaterMark, Environment, RiskState

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


def enforce_equity_drawdown_limit(
    db: Session,
    adapter,
    environment: Environment,
    max_drawdown_pct: D = D("2"),
) -> D | None:
    """Update the durable equity peak and block new orders at the drawdown limit.

    Adapters without a balance API are test doubles/legacy adapters and skip this
    exchange-backed guard. A real adapter must provide a complete positive
    equity valuation; missing pricing data blocks order submission.
    """
    if not callable(getattr(adapter, "balance", None)):
        return None
    if not max_drawdown_pct.is_finite() or max_drawdown_pct <= 0:
        raise ValueError("max_drawdown_pct must be positive and finite")

    metrics = dashboard_metrics(db, adapter, environment)
    if not metrics["equity_complete"]:
        raise RuntimeError("cannot verify equity drawdown: account valuation is incomplete")
    equity = D(str(metrics["equity_usdt"]))
    if not equity.is_finite() or equity <= 0:
        raise RuntimeError("cannot verify equity drawdown: equity must be positive and finite")

    mark = db.scalar(
        select(EquityHighWaterMark).where(EquityHighWaterMark.environment == environment.value)
    )
    if mark is None:
        try:
            with db.begin_nested():
                mark = EquityHighWaterMark(
                    environment=environment.value,
                    current_equity=equity,
                    peak_equity=equity,
                    drawdown_pct=D("0"),
                )
                db.add(mark)
                db.flush()
        except IntegrityError:
            mark = db.scalar(
                select(EquityHighWaterMark).where(EquityHighWaterMark.environment == environment.value)
            )
    if mark is None:
        raise RuntimeError("could not persist equity high-water mark")

    mark.current_equity = equity
    if equity > D(str(mark.peak_equity)):
        mark.peak_equity = equity
    peak = D(str(mark.peak_equity))
    drawdown_pct = max(D("0"), (peak - equity) / peak * D("100"))
    mark.drawdown_pct = drawdown_pct
    db.flush()

    if drawdown_pct >= max_drawdown_pct:
        halt(db, f"equity drawdown {drawdown_pct:.4f}% >= max {max_drawdown_pct:.4f}%")
        # Persist the kill switch before raising so the caller cannot accidentally
        # roll back the halt while handling the rejected order submission.
        db.commit()
        raise RuntimeError(
            f"equity drawdown kill switch active: {drawdown_pct:.4f}% "
            f">= {max_drawdown_pct:.4f}%"
        )
    return drawdown_pct
