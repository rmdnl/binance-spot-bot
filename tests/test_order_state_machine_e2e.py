from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderStatus, SpotOrder
from spot_bot.order_service import _apply_exchange_state, cancel_order, submit_order


class StateMachineAdapter:
    def __init__(self, status):
        self.status = status
        self.cancel_calls = 0

    def validate_order(self, request):
        return None

    def create_order(self, request):
        return {
            "id": "exchange-state-1",
            "status": self.status,
            "filled": "0",
            "average": None,
            "fee": {"cost": "0", "currency": "USDT"},
        }

    def cancel_order(self, exchange_order_id, symbol):
        self.cancel_calls += 1
        return {
            "id": exchange_order_id,
            "status": "canceled",
            "filled": "0",
            "average": None,
            "fee": {"cost": "0", "currency": "USDT"},
        }

    def recent_trades(self, symbol, limit=100):
        return []


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_order(db, status):
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status=status,
        amount=D("0.01"),
        price=D("80000"),
        client_order_id=f"state-{status}",
        exchange_order_id="exchange-state-1",
    )
    db.add(order)
    db.commit()
    return order


@pytest.mark.parametrize(
    ("current", "incoming"),
    [
        (OrderStatus.NEW.value, "open"),
        (OrderStatus.NEW.value, "partially_filled"),
        (OrderStatus.NEW.value, "closed"),
        (OrderStatus.NEW.value, "canceled"),
        (OrderStatus.OPEN.value, "partially_filled"),
        (OrderStatus.OPEN.value, "closed"),
        (OrderStatus.OPEN.value, "canceled"),
        (OrderStatus.PARTIALLY_FILLED.value, "closed"),
        (OrderStatus.PARTIALLY_FILLED.value, "canceled"),
    ],
)
def test_legal_order_state_transitions_are_accepted(current, incoming):
    db = db_session()
    order = make_order(db, current)
    _apply_exchange_state(order, {"id": "exchange-state-1", "status": incoming, "filled": "0", "fee": {}})
    assert order.status == {
        "open": OrderStatus.OPEN.value,
        "partially_filled": OrderStatus.PARTIALLY_FILLED.value,
        "closed": OrderStatus.FILLED.value,
        "canceled": OrderStatus.CANCELED.value,
    }[incoming]


@pytest.mark.parametrize(
    ("current", "incoming"),
    [
        (OrderStatus.FILLED.value, "open"),
        (OrderStatus.FILLED.value, "partially_filled"),
        (OrderStatus.FILLED.value, "canceled"),
        (OrderStatus.CANCELED.value, "new"),
        (OrderStatus.CANCELED.value, "open"),
        (OrderStatus.CANCELED.value, "closed"),
        (OrderStatus.EXPIRED.value, "open"),
        (OrderStatus.REJECTED.value, "open"),
    ],
)
def test_terminal_order_cannot_move_back_to_active_state(current, incoming):
    db = db_session()
    order = make_order(db, current)
    with pytest.raises(ValueError, match="invalid order state transition"):
        _apply_exchange_state(order, {"id": "exchange-state-1", "status": incoming, "filled": "0", "fee": {}})


def test_cancel_never_reopens_a_terminal_order():
    db = db_session()
    order = make_order(db, OrderStatus.FILLED.value)
    adapter = StateMachineAdapter("closed")
    with pytest.raises(ValueError, match="cannot be canceled"):
        cancel_order(db, adapter, order)
    assert adapter.cancel_calls == 0


def test_rejected_order_can_be_recovered_and_resubmitted():
    db = db_session()
    order = make_order(db, OrderStatus.REJECTED.value)
    order.exchange_order_id = None
    adapter = StateMachineAdapter("open")
    result = submit_order(db, adapter, order)
    assert result.order.status == OrderStatus.OPEN.value
    assert result.order.exchange_order_id == "exchange-state-1"
