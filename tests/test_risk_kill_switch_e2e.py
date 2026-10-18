from decimal import Decimal as D
from typing import ClassVar

import pytest
from sqlalchemy import create_engine
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


def test_live_equity_drawdown_guard_persists_peak_and_halts_at_two_percent():
    from sqlalchemy import select
    from spot_bot.models import EquityHighWaterMark, Environment
    from spot_bot.risk_service import enforce_equity_drawdown_limit

    class FakeExchange:
        markets: ClassVar[dict] = {}

    class FakeAdapter:
        exchange = FakeExchange()

        def __init__(self):
            self.equity = "1000"

        def balance(self):
            return {"total": {"USDT": self.equity}}

        def ticker(self, symbol):
            raise AssertionError("USDT equity must not need a market ticker")

    engine = make_db()
    adapter = FakeAdapter()
    with Session(engine) as db:
        assert enforce_equity_drawdown_limit(db, adapter, Environment.TESTNET) == D("0")
        db.commit()
        adapter.equity = "979"
        with pytest.raises(RuntimeError, match="equity drawdown kill switch active"):
            enforce_equity_drawdown_limit(db, adapter, Environment.TESTNET)

    with Session(engine) as db:
        state = db.get(RiskState, 1)
        mark = db.scalar(select(EquityHighWaterMark).where(
            EquityHighWaterMark.environment == Environment.TESTNET.value
        ))
        assert state.halted is True
        assert "2.1000%" in state.reason
        assert mark.peak_equity == D("1000.000000000000000000")
        assert mark.drawdown_pct == D("2.10000000")
    engine.dispose()


def test_equity_drawdown_guard_blocks_order_when_valuation_is_incomplete():
    from spot_bot.models import Environment
    from spot_bot.risk_service import enforce_equity_drawdown_limit

    class FakeExchange:
        markets: ClassVar[dict] = {}

    class FakeAdapter:
        exchange = FakeExchange()

        def balance(self):
            return {"total": {"USDT": "1000", "UNKNOWN": "2"}}

        def ticker(self, symbol):
            raise RuntimeError("unpriced")

    engine = make_db()
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="account valuation is incomplete"):
            enforce_equity_drawdown_limit(db, FakeAdapter(), Environment.TESTNET)
        assert db.get(RiskState, 1) is None
    engine.dispose()
