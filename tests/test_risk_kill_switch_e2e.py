from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, RiskState
from spot_bot.risk_service import assert_trading_allowed, evaluate_drawdown


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_drawdown_breach_persists_halt_and_blocks_trading_after_restart():
    engine = make_db()
    with Session(engine) as db:
        state = evaluate_drawdown(db, D("979"), D("1000"), D("0.02"))
        db.commit()
        assert state.halted is True
        assert "2.1000%" in state.reason
        with pytest.raises(RuntimeError, match="risk halt active"):
            assert_trading_allowed(db)

    with Session(engine) as db:
        state = db.get(RiskState, 1)
        assert state.halted is True
        assert state.reason is not None
        with pytest.raises(RuntimeError, match="risk halt active"):
            assert_trading_allowed(db)


def test_drawdown_below_limit_does_not_halt():
    engine = make_db()
    with Session(engine) as db:
        state = evaluate_drawdown(db, D("990"), D("1000"), D("0.02"))
        db.commit()
        assert state.halted is False
        assert state.reason is None
        assert_trading_allowed(db)
