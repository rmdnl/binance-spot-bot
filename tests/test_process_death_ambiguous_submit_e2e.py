"""Real process-death coverage for the ambiguous exchange-submit window."""

import json
import multiprocessing
import os
from decimal import Decimal as D
from pathlib import Path

import ccxt
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderRequest, OrderSide, OrderStatus, OrderType, SpotOrder
from spot_bot.order_service import create_intent, recover_or_submit


class PersistentExchangeAdapter:
    """File-backed exchange double whose accepted order survives worker death."""

    def __init__(self, state_path: str, *, die_after_accept=False, die_during_recovery=False):
        self.state_path = Path(state_path)
        self.die_after_accept = die_after_accept
        self.die_during_recovery = die_during_recovery

    def _read_state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write_state(self, state):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def validate_order(self, request):
        return None

    def fetch_order_by_client_id(self, symbol, client_order_id):
        state = self._read_state()
        for order in state["orders"]:
            if order["clientOrderId"] == client_order_id and order["symbol"] == symbol:
                return order
        raise ccxt.OrderNotFound(f"no exchange order for {client_order_id}")

    def create_order(self, request):
        state = self._read_state()
        state["submissions"] += 1
        exchange_order = {
            "id": f"exchange-{state['submissions']}",
            "clientOrderId": request.client_order_id,
            "symbol": request.symbol,
            "status": "open",
            "filled": "0",
            "average": None,
            "fee": None,
        }
        state["orders"].append(exchange_order)
        self._write_state(state)

        if self.die_after_accept:
            # Simulate the exchange committing the side effect, then the OS
            # killing the worker before create_order can return a response.
            os._exit(86)
        return exchange_order

    def recent_trades(self, symbol, limit=100):
        if self.die_during_recovery:
            # The order lookup succeeded, but the process dies before the
            # local recovery transaction can commit.
            os._exit(87)
        return []


def _sqlite_engine(url):
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def disable_driver_transaction_control(connection, _record):
        connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def emit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


def _submit_and_die(database_url, exchange_state_path):
    engine = _sqlite_engine(database_url)
    with Session(engine) as db:
        order = db.scalar(
            select(SpotOrder).where(SpotOrder.client_order_id == "process-death-submit-001")
        )
        assert order is not None
        recover_or_submit(
            db,
            PersistentExchangeAdapter(exchange_state_path, die_after_accept=True),
            order,
            post_only=True,
        )


def _recover_and_die_before_commit(database_url, exchange_state_path):
    engine = _sqlite_engine(database_url)
    with Session(engine) as db:
        order = db.scalar(
            select(SpotOrder).where(SpotOrder.client_order_id == "process-death-submit-001")
        )
        assert order is not None
        recover_or_submit(
            db,
            PersistentExchangeAdapter(exchange_state_path, die_during_recovery=True),
            order,
            post_only=True,
        )


def _run_process(target, args, expected_exit_code):
    process = multiprocessing.get_context("fork").Process(target=target, args=args)
    process.start()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        raise AssertionError("crash worker did not exit within 10 seconds")
    assert process.exitcode == expected_exit_code


def test_process_death_after_exchange_accept_then_restart_recovers_by_client_id_without_resubmit(
    tmp_path,
):
    database_path = tmp_path / "ambiguous-submit.sqlite"
    database_url = f"sqlite:///{database_path}"
    exchange_state_path = tmp_path / "exchange-state.json"
    exchange_state_path.write_text(
        json.dumps({"submissions": 0, "orders": []}), encoding="utf-8"
    )

    engine = _sqlite_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        order = create_intent(
            db,
            OrderRequest(
                Environment.TESTNET,
                "BTC/USDT",
                OrderSide.BUY,
                OrderType.LIMIT,
                D("0.01"),
                D("80000"),
                "process-death-submit-001",
                True,
            ),
        )
        db.commit()
        order_id = order.id
    engine.dispose()

    # First worker: the exchange accepts the order, persists its remote state,
    # and the OS kills the process before any exchange response is returned.
    _run_process(
        _submit_and_die,
        (database_url, str(exchange_state_path)),
        expected_exit_code=86,
    )
    exchange_state = json.loads(exchange_state_path.read_text(encoding="utf-8"))
    assert exchange_state["submissions"] == 1
    assert len(exchange_state["orders"]) == 1
    assert exchange_state["orders"][0]["clientOrderId"] == "process-death-submit-001"

    # The local intent must still be durable but unresolved after the first death.
    engine = _sqlite_engine(database_url)
    with Session(engine) as db:
        order = db.get(SpotOrder, order_id)
        assert order is not None
        assert order.status == OrderStatus.NEW.value
        assert order.exchange_order_id is None
    engine.dispose()

    # Second worker: lookup finds the remote order, but this worker also dies
    # before its recovery transaction commits. The next restart must recover again.
    _run_process(
        _recover_and_die_before_commit,
        (database_url, str(exchange_state_path)),
        expected_exit_code=87,
    )

    engine = _sqlite_engine(database_url)
    with Session(engine) as db:
        order = db.get(SpotOrder, order_id)
        assert order is not None
        assert order.status == OrderStatus.NEW.value
        assert order.exchange_order_id is None

        recovered = recover_or_submit(
            db,
            PersistentExchangeAdapter(str(exchange_state_path)),
            order,
            post_only=True,
        )
        db.commit()

        assert recovered.exchange is not None
        assert recovered.order.exchange_order_id == "exchange-1"
        assert recovered.order.status == OrderStatus.OPEN.value
        assert db.scalar(select(func.count()).select_from(SpotOrder)) == 1

    # Recovery and another lookup must never create a second exchange order.
    exchange_state = json.loads(exchange_state_path.read_text(encoding="utf-8"))
    assert exchange_state["submissions"] == 1
    assert len(exchange_state["orders"]) == 1
    engine.dispose()
