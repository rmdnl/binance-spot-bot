from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Thread

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderRequest, OrderSide, OrderType, RiskState, SpotOrder
from spot_bot.order_service import create_intent
from spot_bot.risk_service import evaluate_drawdown


def make_db(path: Path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine


def request(client_id):
    return OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000"), client_id, False)


def test_concurrent_halt_and_order_admission_have_one_durable_winner():
    with TemporaryDirectory() as tmp:
        engine = make_db(Path(tmp) / "risk.db")
        ready = Barrier(2)
        results = []

        def halt_worker():
            with Session(engine) as db:
                ready.wait()
                evaluate_drawdown(db, D("970"), D("1000"), D("0.02"))
                db.commit()
                results.append("halt")

        def order_worker():
            with Session(engine) as db:
                ready.wait()
                try:
                    create_intent(db, request("concurrent-order"))
                    db.commit()
                    results.append("order")
                except RuntimeError:
                    db.rollback()
                    results.append("blocked")

        t1 = Thread(target=halt_worker)
        t2 = Thread(target=order_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        with Session(engine) as db:
            state = db.get(RiskState, 1)
            order = db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == "concurrent-order"))
            assert state is not None
            assert state.halted is True
            assert sorted(results) in (["blocked", "halt"], ["halt", "order"])
            if "order" in results:
                assert order is not None
            else:
                assert order is None


def test_repeated_concurrent_admission_cannot_create_orders_after_persisted_halt():
    with TemporaryDirectory() as tmp:
        engine = make_db(Path(tmp) / "risk.db")
        with Session(engine) as db:
            evaluate_drawdown(db, D("970"), D("1000"), D("0.02"))
            db.commit()

        barrier = Barrier(4)
        results = []

        def worker(index):
            with Session(engine) as db:
                barrier.wait()
                try:
                    create_intent(db, request(f"blocked-{index}"))
                    db.commit()
                    results.append("order")
                except RuntimeError:
                    db.rollback()
                    results.append("blocked")

        threads = [Thread(target=worker, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        with Session(engine) as db:
            assert results == ["blocked", "blocked", "blocked"]
            assert db.scalar(select(SpotOrder)) is None
            assert db.get(RiskState, 1).halted is True
