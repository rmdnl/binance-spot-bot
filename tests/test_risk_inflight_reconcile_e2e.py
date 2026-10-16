from decimal import Decimal as D
from unittest.mock import Mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderRequest, OrderSide, OrderStatus, OrderType, RiskState, SpotOrder
from spot_bot.order_service import create_intent, reconcile_order
from spot_bot.risk_service import halt


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_inflight_order_reconciles_after_kill_switch_without_duplicate_inventory():
    engine = make_db()
    adapter = Mock()
    adapter.fetch_order_by_client_id.return_value = {
        "id": "exchange-1",
        "status": "closed",
        "filled": "0.010",
        "average": "80000",
        "fee": {"cost": "0.80", "currency": "USDT"},
    }
    with Session(engine) as db:
        request = OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000"), "inflight-1", False)
        order = create_intent(db, request)
        order.status = OrderStatus.OPEN.value
        order.exchange_order_id = "exchange-1"
        db.commit()

    with Session(engine) as db:
        halt(db, "test kill switch")
        db.commit()

    with Session(engine) as db:
        order = db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == "inflight-1"))
        reconcile_order(db, adapter, order)
        db.commit()
        assert order.status == OrderStatus.FILLED.value
        assert order.filled_amount == D("0.010")
        assert order.fee_quote == D("0.80000000")
        assert db.get(RiskState, 1).halted is True
        assert adapter.fetch_order_by_client_id.call_count == 1

        with Session(engine) as check:
            persisted = check.scalar(select(SpotOrder).where(SpotOrder.client_order_id == "inflight-1"))
            assert persisted.status == OrderStatus.FILLED.value
            assert persisted.filled_amount == D("0.010")


def test_reconcile_is_idempotent_after_kill_switch():
    engine = make_db()
    adapter = Mock()
    adapter.fetch_order.return_value = {
        "id": "exchange-2",
        "status": "closed",
        "filled": "0.010",
        "average": "80000",
        "fee": {"cost": "0.80", "currency": "USDT"},
    }
    with Session(engine) as db:
        request = OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000"), "inflight-2", False)
        order = create_intent(db, request)
        order.status = OrderStatus.OPEN.value
        order.exchange_order_id = "exchange-2"
        db.commit()

    with Session(engine) as db:
        halt(db, "test kill switch")
        db.commit()

    with Session(engine) as db:
        order = db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == "inflight-2"))
        reconcile_order(db, adapter, order)
        db.commit()

    with Session(engine) as db:
        order = db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == "inflight-2"))
        reconcile_order(db, adapter, order)
        db.commit()
        assert order.status == OrderStatus.FILLED.value
        assert order.filled_amount == D("0.010")
