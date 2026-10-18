from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from spot_bot.models import Base, Environment, OrderStatus, SpotOrder


def test_stale_reconcile_worker_cannot_regress_terminal_order_state(tmp_path):
    database_path = tmp_path / "concurrent-order-state.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    with Session(engine) as setup:
        order = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            order_type="limit",
            status=OrderStatus.OPEN.value,
            amount=D("0.01"),
            price=D("80000"),
            client_order_id="concurrent-state-001",
            exchange_order_id="exchange-concurrent-state-001",
        )
        setup.add(order)
        setup.commit()
        order_id = order.id

    stale_worker = Session(engine)
    terminal_worker = Session(engine)
    try:
        stale_order = stale_worker.get(SpotOrder, order_id)
        terminal_order = terminal_worker.get(SpotOrder, order_id)
        assert stale_order is not None
        assert terminal_order is not None
        assert stale_order.version_id == terminal_order.version_id

        # Reconcile worker observes an open order. Before it persists its
        # response, another worker commits the authoritative terminal fill.
        terminal_order.status = OrderStatus.FILLED.value
        terminal_order.filled_amount = D("0.01")
        terminal_worker.commit()

        # The stale worker then attempts to persist an older cancel response.
        stale_order.status = OrderStatus.CANCELED.value
        with pytest.raises(StaleDataError):
            stale_worker.commit()
        stale_worker.rollback()

        with Session(engine) as verify:
            persisted = verify.scalar(
                select(SpotOrder).where(SpotOrder.id == order_id)
            )
            assert persisted is not None
            assert persisted.status == OrderStatus.FILLED.value
            assert persisted.filled_amount == D("0.01")
            assert persisted.version_id == 2
    finally:
        stale_worker.close()
        terminal_worker.close()
        engine.dispose()
