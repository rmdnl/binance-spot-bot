import multiprocessing
import os
from decimal import Decimal as D

import ccxt
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from spot_bot.grid_engine import initial_order_plans, submit_grid_order
from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    GridBot,
    GridLevel,
    InventoryApplication,
    InventoryPosition,
    OrderStatus,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)
from spot_bot.order_service import recover_or_submit, record_fills


class CrashMatrixAdapter:
    def __init__(self, exchange_status="open", filled="0", trades=None):
        self.exchange_status = exchange_status
        self.filled = filled
        self.trades = trades or []
        self.orders = {}
        self.submissions = 0
        self.lookup_count = 0
        self.crash_after_create = False

    def validate_order(self, request):
        return None

    def create_order(self, request):
        self.submissions += 1
        exchange_id = f"exchange-{self.submissions}"
        self.orders[exchange_id] = {
            "id": exchange_id,
            "status": self.exchange_status,
            "filled": self.filled,
            "average": "80000" if D(self.filled) else None,
            "fee": {"cost": "0.08" if D(self.filled) else "0", "currency": "USDT"},
        }
        if self.crash_after_create:
            self.crash_after_create = False
            raise ConnectionError("simulated crash after exchange accepted order")
        return self.orders[exchange_id]

    def fetch_order_by_client_id(self, symbol, client_order_id):
        self.lookup_count += 1
        if self.orders:
            return next(iter(self.orders.values()))
        raise ccxt.OrderNotFound("not found")

    def fetch_order(self, exchange_order_id, symbol):
        return self.orders[exchange_order_id]

    def recent_trades(self, symbol, limit=100):
        return self.trades


def sqlite_engine(url):
    engine = create_engine(url)

    # Disable sqlite3's legacy transaction control and let SQLAlchemy
    # emit BEGIN explicitly, so SAVEPOINT operations roll back correctly.
    @event.listens_for(engine, "connect")
    def disable_driver_transaction_control(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def emit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


def db_session():
    engine = sqlite_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _crash_process_before_commit(database_url, trade):
    # This runs in a separate OS process. os._exit intentionally bypasses
    # Python cleanup handlers and Session.close(), like an abrupt process death.
    engine = sqlite_engine(database_url)
    with Session(engine) as db:
        order = db.scalar(
            select(SpotOrder).where(
                SpotOrder.client_order_id == "crash-process-001"
            )
        )
        adapter = CrashMatrixAdapter(trades=[trade])
        assert order is not None
        assert record_fills(db, adapter, order) == 1
        os._exit(73)


def make_bot(db):
    bot = GridBot(
        environment="testnet",
        symbol="BTC/USDT",
        grid_type="arithmetic",
        lower_price=D("80000"),
        upper_price=D("90000"),
        grids=11,
        capital_quote=D("500"),
        max_open_orders=1,
        status="running",
    )
    db.add(bot)
    db.flush()
    for index in range(11):
        db.add(
            GridLevel(
                bot_id=bot.id,
                level_index=index,
                price=D(80000 + index * 1000),
            )
        )
    db.commit()
    return bot


def make_position(db):
    position = InventoryPosition(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        base_quantity=D("0"),
        quote_cost=D("0"),
    )
    db.add(position)
    db.commit()
    return position


def counts(db):
    return {
        "fills": db.scalar(select(func.count()).select_from(SpotFill)),
        "applications": db.scalar(select(func.count()).select_from(InventoryApplication)),
        "pnl": db.scalar(select(func.count()).select_from(RealizedPNLEntry)),
    }


def test_crash_matrix_exchange_accept_then_restart_recovers_without_duplicate():
    db = db_session()
    bot = make_bot(db)
    adapter = CrashMatrixAdapter()
    adapter.crash_after_create = True
    plan = initial_order_plans(bot, D("85000"))[0]

    try:
        submit_grid_order(db, adapter, bot, plan)
    except ConnectionError:
        db.rollback()

    order = db.scalar(select(SpotOrder).order_by(SpotOrder.id.desc()))
    assert order.status == OrderStatus.NEW.value
    assert adapter.submissions == 1

    recovered = recover_or_submit(db, adapter, order, post_only=True)
    db.commit()
    assert recovered.exchange is not None
    assert recovered.order.exchange_order_id == "exchange-1"
    assert adapter.submissions == 1
    assert counts(db) == {"fills": 0, "applications": 0, "pnl": 0}


def test_crash_matrix_filled_order_reconcile_is_idempotent():
    db = db_session()
    make_position(db)
    adapter = CrashMatrixAdapter(
        exchange_status="closed",
        filled="0.01",
        trades=[
            {
                "id": "trade-crash-filled-1",
                "order": "exchange-1",
                "amount": "0.01",
                "price": "80000",
                "cost": "800",
                "fee": {"cost": "0.08", "currency": "USDT"},
            }
        ],
    )
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status=OrderStatus.NEW.value,
        amount=D("0.01"),
        price=D("80000"),
        client_order_id="crash-matrix-filled-001",
    )
    db.add(order)
    db.commit()
    adapter.orders["exchange-1"] = {
        "id": "exchange-1",
        "status": "closed",
        "filled": "0.01",
        "average": "80000",
        "fee": {"cost": "0.08", "currency": "USDT"},
    }

    recovered = recover_or_submit(db, adapter, order, post_only=True)
    db.commit()
    fill = db.scalar(select(SpotFill).where(SpotFill.order_id == order.id))
    assert recovered.order.status == OrderStatus.FILLED.value
    assert fill is not None
    assert apply_fill(db, fill).applied is False
    db.commit()

    before = counts(db)
    assert record_fills(db, adapter, order) == 0
    db.commit()
    assert counts(db) == before
    position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
    assert position.base_quantity == D("0.010000000000")
    assert position.quote_cost == D("800.080000000000")


def test_crash_matrix_partial_fill_reconcile_then_second_reconcile_no_duplicate():
    db = db_session()
    make_position(db)
    adapter = CrashMatrixAdapter(
        exchange_status="open",
        filled="0.005",
        trades=[
            {
                "id": "trade-crash-partial-1",
                "order": "exchange-1",
                "amount": "0.005",
                "price": "80000",
                "cost": "400",
                "fee": {"cost": "0.04", "currency": "USDT"},
            }
        ],
    )
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status=OrderStatus.NEW.value,
        amount=D("0.01"),
        price=D("80000"),
        client_order_id="crash-matrix-partial-001",
        exchange_order_id="exchange-1",
    )
    db.add(order)
    db.commit()
    adapter.orders["exchange-1"] = {
        "id": "exchange-1",
        "status": "open",
        "filled": "0.005",
        "average": "80000",
        "fee": {"cost": "0.04", "currency": "USDT"},
    }

    adapter.fetch_order_by_client_id("BTC/USDT", order.client_order_id)
    adapter.orders["exchange-1"]["status"] = "canceled"
    adapter.orders["exchange-1"]["filled"] = "0.005"
    record_fills(db, adapter, order)
    fill = db.scalar(select(SpotFill).where(SpotFill.order_id == order.id))
    assert fill.amount == D("0.005")
    assert apply_fill(db, fill).applied is False
    db.commit()

    before = counts(db)
    assert record_fills(db, adapter, order) == 0
    db.commit()
    assert counts(db) == before
    position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
    assert position.base_quantity == D("0.005000000000")
    assert position.quote_cost == D("400.040000000000")


def test_crash_matrix_accounting_rollback_then_restart_applies_once():
    db = db_session()
    make_position(db)
    adapter = CrashMatrixAdapter(
        trades=[
            {
                "id": "trade-crash-rollback-1",
                "order": "exchange-rollback-1",
                "amount": "0.01",
                "price": "80000",
                "cost": "800",
                "fee": {"cost": "0.08", "currency": "USDT"},
            }
        ],
    )
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status=OrderStatus.FILLED.value,
        amount=D("0.01"),
        price=D("80000"),
        client_order_id="crash-rollback-001",
        exchange_order_id="exchange-rollback-1",
    )
    db.add(order)
    db.commit()

    # Simulate a crash before the fill/accounting transaction commits.
    assert record_fills(db, adapter, order) == 1
    assert counts(db) == {"fills": 1, "applications": 1, "pnl": 0}

    position = db.scalar(
        select(InventoryPosition).where(
            InventoryPosition.environment == Environment.TESTNET.value,
            InventoryPosition.symbol == "BTC/USDT",
        )
    )
    assert position.base_quantity == D("0.010000000000")
    assert position.quote_cost == D("800.080000000000")

    db.rollback()

    # Rollback must undo both fill persistence and inventory accounting.
    assert counts(db) == {"fills": 0, "applications": 0, "pnl": 0}
    position = db.scalar(
        select(InventoryPosition).where(
            InventoryPosition.environment == Environment.TESTNET.value,
            InventoryPosition.symbol == "BTC/USDT",
        )
    )
    assert position.base_quantity == D("0")
    assert position.quote_cost == D("0")

    # Restart/reconcile discovers the exchange trade and applies it once.
    assert record_fills(db, adapter, order) == 1
    db.commit()
    assert counts(db) == {"fills": 1, "applications": 1, "pnl": 0}

    position = db.scalar(
        select(InventoryPosition).where(
            InventoryPosition.environment == Environment.TESTNET.value,
            InventoryPosition.symbol == "BTC/USDT",
        )
    )
    assert position.base_quantity == D("0.010000000000")
    assert position.quote_cost == D("800.080000000000")

    # A repeated reconcile must not duplicate inventory or PnL.
    assert record_fills(db, adapter, order) == 0
    db.commit()
    assert counts(db) == {"fills": 1, "applications": 1, "pnl": 0}
    assert position.base_quantity == D("0.010000000000")
    assert position.quote_cost == D("800.080000000000")



def test_crash_matrix_real_process_death_then_restart_replays_once(tmp_path):
    database_path = tmp_path / "crash-matrix.sqlite"
    database_url = f"sqlite:///{database_path}"
    engine = sqlite_engine(database_url)
    Base.metadata.create_all(engine)

    trade = {
        "id": "trade-process-crash-1",
        "order": "exchange-process-crash-1",
        "amount": "0.01",
        "price": "80000",
        "cost": "800",
        "fee": {"cost": "0.08", "currency": "USDT"},
    }

    with Session(engine) as db:
        make_position(db)
        order = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            order_type="limit",
            status=OrderStatus.FILLED.value,
            amount=D("0.01"),
            price=D("80000"),
            client_order_id="crash-process-001",
            exchange_order_id="exchange-process-crash-1",
        )
        db.add(order)
        db.commit()

    engine.dispose()

    # The child persists a fill and applies inventory in an open transaction,
    # then dies without commit or graceful Python/SQLAlchemy cleanup.
    process = multiprocessing.get_context("fork").Process(
        target=_crash_process_before_commit,
        args=(database_url, trade),
    )
    process.start()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        raise AssertionError("crash worker did not exit within 10 seconds")
    assert process.exitcode == 73

    # Re-open the file-backed DB in a fresh engine/session, as a restarted bot
    # would. The interrupted transaction must have left no partial accounting.
    engine = sqlite_engine(database_url)
    with Session(engine) as db:
        assert counts(db) == {"fills": 0, "applications": 0, "pnl": 0}
        position = db.scalar(
            select(InventoryPosition).where(
                InventoryPosition.environment == Environment.TESTNET.value,
                InventoryPosition.symbol == "BTC/USDT",
            )
        )
        assert position.base_quantity == D("0")
        assert position.quote_cost == D("0")

        order = db.scalar(
            select(SpotOrder).where(
                SpotOrder.client_order_id == "crash-process-001"
            )
        )
        adapter = CrashMatrixAdapter(trades=[trade])
        assert record_fills(db, adapter, order) == 1
        db.commit()

        assert counts(db) == {"fills": 1, "applications": 1, "pnl": 0}
        db.refresh(position)
        assert position.base_quantity == D("0.010000000000")
        assert position.quote_cost == D("800.080000000000")

        # A second restart/reconcile must not reapply the same exchange trade.
        assert record_fills(db, adapter, order) == 0
        db.commit()
        assert counts(db) == {"fills": 1, "applications": 1, "pnl": 0}
        db.refresh(position)
        assert position.base_quantity == D("0.010000000000")
        assert position.quote_cost == D("800.080000000000")

    engine.dispose()
