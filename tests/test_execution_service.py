from decimal import Decimal as D

import pytest

from dca_bot.execution import ExecutionError, OrderResult
from dca_bot.execution_service import ExecutionService
from dca_bot.models import ExchangeOrderRecord, StrategyRecord


class FakeAdapter:
    def __init__(self, result=None, error=None, recovery=None):
        self.result = result
        self.error = error
        self.recovery = recovery
        self.place_calls = 0
        self.fetch_calls = 0

    def place_limit(self, intent):
        self.place_calls += 1
        if self.error:
            raise self.error
        return self.result

    def fetch_order_by_client_id(self, symbol, client_order_id):
        self.fetch_calls += 1
        return self.recovery

    def fetch_order(self, symbol, order_id):
        raise AssertionError("not used")

    def fetch_open_orders(self, symbol=None):
        raise AssertionError("not used")

    def cancel_order(self, symbol, order_id):
        raise AssertionError("not used")


def _session(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from dca_bot.db import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _strategy(session):
    row = StrategyRecord(name="test", config_json="{}", enabled=True)
    session.add(row)
    session.commit()
    return row


def _record(session, strategy, *, client_order_id="dca-1", status="intent", exchange_order_id=None):
    row = ExchangeOrderRecord(
        strategy_id=strategy.id,
        symbol="BTC/USDT",
        side="buy",
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        status=status,
        amount="0.001",
        price="50000",
        filled="0",
        remaining="0.001",
        raw_json="{}",
    )
    session.add(row)
    session.commit()
    return row


def _result(status="open"):
    return OrderResult(
        order_id="123",
        client_order_id="dca-1",
        symbol="BTC/USDT",
        side="buy",
        status=status,
        amount=D("0.001"),
        filled=D("0"),
        remaining=D("0.001"),
        average=None,
        price=D("50000"),
        raw={"id": "123"},
    )


def test_submit_persists_result(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    record = _record(session, strategy)
    adapter = FakeAdapter(result=_result())

    outcome = ExecutionService(adapter, session).submit_or_recover(record)

    assert outcome.record.exchange_order_id == "123"
    assert outcome.record.status == "open"
    assert outcome.result.order_id == "123"
    assert outcome.submitted is True
    assert adapter.fetch_calls == 1
    assert adapter.place_calls == 1


def test_existing_submitted_order_is_not_retried(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    record = _record(session, strategy, status="open", exchange_order_id="123")
    adapter = FakeAdapter(result=_result())

    outcome = ExecutionService(adapter, session).submit_or_recover(record)

    assert outcome.result is None
    assert outcome.submitted is False
    assert adapter.fetch_calls == 0
    assert adapter.place_calls == 0


def test_recovery_by_client_order_id_does_not_resubmit(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    record = _record(session, strategy)
    adapter = FakeAdapter(recovery=[_result()])

    outcome = ExecutionService(adapter, session).submit_or_recover(record)

    assert outcome.record.exchange_order_id == "123"
    assert outcome.record.status == "open"
    assert outcome.submitted is False
    assert adapter.fetch_calls == 1
    assert adapter.place_calls == 0


def test_submission_error_becomes_unknown_and_blocks_retry(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    record = _record(session, strategy, client_order_id="dca-timeout")
    adapter = FakeAdapter(error=TimeoutError("timeout"))

    with pytest.raises(TimeoutError):
        ExecutionService(adapter, session).submit_or_recover(record)

    session.refresh(record)
    assert record.status == "unknown"

    adapter.error = None
    with pytest.raises(ExecutionError, match="reconcile"):
        ExecutionService(adapter, session).submit_or_recover(record)
    assert adapter.place_calls == 1


def test_missing_exchange_order_id_becomes_unknown(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    record = _record(session, strategy, client_order_id="dca-no-id")
    adapter = FakeAdapter(result=OrderResult(
        order_id=None,
        client_order_id="dca-no-id",
        symbol="BTC/USDT",
        side="buy",
        status="open",
        amount=D("0.001"),
        filled=D("0"),
        remaining=D("0.001"),
        average=None,
        price=D("50000"),
        raw={},
    ))

    with pytest.raises(ExecutionError, match="order id"):
        ExecutionService(adapter, session).submit_or_recover(record)

    session.refresh(record)
    assert record.status == "unknown"
