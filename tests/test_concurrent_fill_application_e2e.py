from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal as D
from threading import Barrier

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)


def _create_fill(db, *, client_order_id, exchange_order_id, trade_id, amount, price):
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status="filled",
        amount=amount,
        price=price,
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
    )
    db.add(order)
    db.flush()
    fill = SpotFill(
        order_id=order.id,
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        price=price,
        amount=amount,
        quote_amount=amount * price,
        fee=D("0.08"),
        fee_currency="USDT",
        exchange_trade_id=trade_id,
        timestamp=datetime.now(UTC),
    )
    db.add(fill)
    db.flush()
    return fill


def test_two_workers_applying_the_same_fill_only_mutate_inventory_once(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'same-fill-application.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        fill = _create_fill(
            db,
            client_order_id="same-fill-application",
            exchange_order_id="exchange-same-fill",
            trade_id="trade-same-fill",
            amount=D("0.01"),
            price=D("80000"),
        )
        db.commit()
        fill_id = fill.id

    start = Barrier(2)

    def worker():
        with Session(engine) as db:
            fill = db.get(SpotFill, fill_id)
            assert fill is not None
            start.wait()
            result = apply_fill(db, fill)
            db.commit()
            return result.applied

    with ThreadPoolExecutor(max_workers=2) as pool:
        applied_results = list(pool.map(lambda _: worker(), range(2)))

    with Session(engine) as db:
        position = db.scalar(
            select(InventoryPosition).where(
                InventoryPosition.environment == Environment.TESTNET.value,
                InventoryPosition.symbol == "BTC/USDT",
            )
        )
        assert sorted(applied_results) == [False, True]
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0
        assert position is not None
        assert position.base_quantity == D("0.01")
        assert position.quote_cost == D("800.08")


def test_two_distinct_concurrent_fills_preserve_both_inventory_updates(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'distinct-concurrent-fills.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        fills = [
            _create_fill(
                db,
                client_order_id=f"distinct-fill-{index}",
                exchange_order_id=f"exchange-distinct-{index}",
                trade_id=f"trade-distinct-{index}",
                amount=D("0.01"),
                price=D("80000") + D(index * 100),
            )
            for index in (1, 2)
        ]
        db.commit()
        fill_ids = [fill.id for fill in fills]

    start = Barrier(2)

    def worker(fill_id):
        with Session(engine) as db:
            fill = db.get(SpotFill, fill_id)
            assert fill is not None
            start.wait()
            result = apply_fill(db, fill)
            db.commit()
            return result.applied

    with ThreadPoolExecutor(max_workers=2) as pool:
        applied_results = list(pool.map(worker, fill_ids))

    with Session(engine) as db:
        position = db.scalar(
            select(InventoryPosition).where(
                InventoryPosition.environment == Environment.TESTNET.value,
                InventoryPosition.symbol == "BTC/USDT",
            )
        )
        assert applied_results == [True, True]
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 2
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0
        assert position is not None
        assert position.base_quantity == D("0.02")
        assert position.quote_cost == D("1600.16")
