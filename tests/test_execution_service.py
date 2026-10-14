from decimal import Decimal as D

import pytest

from dca_bot.execution import ExecutionError, OrderIntent, OrderResult
from dca_bot.execution_service import submit_idempotent
from dca_bot.models import ExchangeOrderRecord, StrategyRecord


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def place_limit(self, intent):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

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


def _result():
    return OrderResult(
        order_id="123",
        client_order_id="dca-1",
        symbol="BTC/USDT",
        side="buy",
        status="open",
        amount=D("0.001"),
        filled=D("0"),
        remaining=D("0.001"),
        average=None,
        price=D("50000"),
        raw={"id": "123"},
    )


def test_submit_persists_intent_and_result(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    adapter = FakeAdapter(result=_result())
    row, result = submit_idempotent(
        session,
        adapter,
        strategy_id=strategy.id,
        intent=OrderIntent("BTC/USDT", "buy", D("0.001"), D("50000"), "dca-1"),
    )
    assert row.exchange_order_id == "123"
    assert row.status == "open"
    assert result.order_id == "123"
    assert adapter.calls == 1


def test_existing_submitted_order_is_not_retried(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    adapter = FakeAdapter(result=_result())
    intent = OrderIntent("BTC/USDT", "buy", D("0.001"), D("50000"), "dca-1")
    submit_idempotent(session, adapter, strategy_id=strategy.id, intent=intent)
    row, result = submit_idempotent(session, adapter, strategy_id=strategy.id, intent=intent)
    assert row.exchange_order_id == "123"
    assert result is None
    assert adapter.calls == 1


def test_submission_error_becomes_unknown_and_blocks_retry(tmp_path):
    session = _session(tmp_path)
    strategy = _strategy(session)
    adapter = FakeAdapter(error=TimeoutError("timeout"))
    intent = OrderIntent("BTC/USDT", "buy", D("0.001"), D("50000"), "dca-timeout")
    with pytest.raises(TimeoutError):
        submit_idempotent(session, adapter, strategy_id=strategy.id, intent=intent)
    row = session.query(ExchangeOrderRecord).one()
    assert row.status == "unknown"

    adapter.error = None
    with pytest.raises(ExecutionError, match="reconcile"):
        submit_idempotent(session, adapter, strategy_id=strategy.id, intent=intent)
    assert adapter.calls == 1
