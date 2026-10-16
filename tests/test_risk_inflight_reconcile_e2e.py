from decimal import Decimal as D
from unittest.mock import Mock

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderRequest, OrderSide, OrderStatus, OrderType, RiskState, SpotFill, SpotOrder
from spot_bot.order_service import create_intent, reconcile_order
from spot_bot.risk_service import halt


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def request(client_id):
    return OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000"), client_id, False)


def filled_exchange_response(exchange_id):
    return {"id": exchange_id, "status": "closed", "filled": "0.010", "average": "80000", "fee": {"cost": "0.80", "currency": "USDT"}}


def trade(exchange_id, trade_id):
    return {"id": trade_id, "order": exchange_id, "amount": "0.010", "price": "80000", "cost": "800.00", "fee": {"cost": "0.80", "currency": "USDT"}}


def test_inflight_order_reconciles_after_kill_switch_without_duplicate_inventory():
    engine = make_db()
    adapter = Mock()
    adapter.fetch_order.return_value = filled_exchange_response("exchange-1")
    adapter.recent_trades.return_value = [trade("exchange-1", "trade-1")]

    with Session(engine) as db:
        order = create_intent(db, request("inflight-1"))
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
        assert order.fee_quote.quantize(D("0.00000001")) == D("0.80000000")
        assert db.get(RiskState, 1).halted is True
        assert db.scalar(select(func.count()).select_from(SpotFill).where(SpotFill.order_id == order.id)) == 1


def test_reconcile_is_idempotent_after_kill_switch_and_does_not_double_count_fill():
    engine = make_db()
    adapter = Mock()
    adapter.fetch_order.return_value = filled_exchange_response("exchange-2")
    adapter.recent_trades.return_value = [trade("exchange-2", "trade-2")]

    with Session(engine) as db:
        order = create_intent(db, request("inflight-2"))
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
        assert db.scalar(select(func.count()).select_from(SpotFill).where(SpotFill.order_id == order.id)) == 1
        assert order.fee_quote.quantize(D("0.00000001")) == D("0.80000000")
