from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderRequest, OrderSide, OrderType, RiskState, SpotOrder
from spot_bot.order_service import create_intent
from spot_bot.risk_service import evaluate_drawdown


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def request():
    return OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000"), "race-order", False)


def test_halt_wins_when_order_admission_runs_after_halt_commit():
    engine = make_db()
    with Session(engine) as db:
        evaluate_drawdown(db, D("970"), D("1000"), D("0.02"))
        db.commit()

    with Session(engine) as db, pytest.raises(RuntimeError, match="risk halt active"):
        create_intent(db, request())
        db.rollback()
    with Session(engine) as db:
        assert db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == "race-order")) is None
        assert db.get(RiskState, 1).halted is True


def test_order_admitted_before_halt_then_halt_persists_without_retroactive_cancel():
    engine = make_db()
    with Session(engine) as db:
        create_intent(db, request())
        db.commit()

    with Session(engine) as db:
        evaluate_drawdown(db, D("970"), D("1000"), D("0.02"))
        db.commit()

    with Session(engine) as db:
        order = db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == "race-order"))
        assert order is not None
        assert order.status == "new"
        assert db.get(RiskState, 1).halted is True


def test_halted_state_survives_new_session_and_blocks_admission():
    engine = make_db()
    with Session(engine) as db:
        evaluate_drawdown(db, D("970"), D("1000"), D("0.02"))
        db.commit()

    with Session(engine) as db, pytest.raises(RuntimeError, match="risk halt active"):
        create_intent(db, request())
