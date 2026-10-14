from decimal import Decimal as D

import pytest

from dca_bot.execution_service import ExecutionService
from dca_bot.models import ExchangeOrderRecord, StrategyRecord


class FakeAdapter:
    def __init__(self, existing=None, result=None, error=None):
        self.existing = existing or []
        self.result = result
        self.error = error
        self.lookups = 0
        self.submissions = 0

    def fetch_order_by_client_id(self, symbol, client_order_id):
        self.lookups += 1
        return self.existing

    def place_limit(self, intent):
        self.submissions += 1
        if self.error:
            raise self.error
        return self.result


def make_record(db_session):
    strategy = StrategyRecord(name="gateway", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    record = ExchangeOrderRecord(
        strategy_id=strategy.id, symbol="BTC/USDT", side="buy", client_order_id="entry-1",
        status="intent", amount="1", price="100", filled="0", last_accounted_filled="0",
        remaining="1", raw_json="{}",
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_execution_service_recovers_existing_order_without_resubmission(db_session):
    record = make_record(db_session)
    from dca_bot.execution import OrderResult
    result = OrderResult("42", "entry-1", "BTC/USDT", "buy", "open", D("1"), D("0"), D("1"), None, D("100"), {})
    adapter = FakeAdapter(existing=[result])
    outcome = ExecutionService(adapter, db_session).submit_or_recover(record)
    assert not outcome.submitted
    assert adapter.submissions == 0
    assert record.exchange_order_id == "42"


def test_execution_service_does_not_blindly_retry_ambiguous_submission(db_session):
    record = make_record(db_session)
    adapter = FakeAdapter(error=TimeoutError("network timeout"))
    with pytest.raises(TimeoutError):
        ExecutionService(adapter, db_session).submit_or_recover(record)
    assert adapter.submissions == 1
    assert record.status == "intent"
