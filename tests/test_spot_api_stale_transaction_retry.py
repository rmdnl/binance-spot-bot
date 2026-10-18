from types import SimpleNamespace

import pytest
from sqlalchemy.orm.exc import StaleDataError

from spot_bot.spot_api import _commit_order_transaction


class FakeSession:
    def __init__(self):
        self.rollbacks = 0
        self.commits = 0

    def get(self, model, order_id, populate_existing=False):
        assert order_id == 17
        assert populate_existing is True
        return SimpleNamespace(id=order_id, status="open")

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


def test_stale_order_transaction_rolls_back_then_reloads_and_retries():
    db = FakeSession()
    calls = []

    def operation(order):
        calls.append(order)
        if len(calls) == 1:
            raise StaleDataError("stale version")
        return "accounted"

    order, result = _commit_order_transaction(db, 17, operation)

    assert order.id == 17
    assert result == "accounted"
    assert len(calls) == 2
    assert calls[0] is not calls[1]
    assert db.rollbacks == 1
    assert db.commits == 1


def test_stale_order_transaction_does_not_retry_more_than_once():
    db = FakeSession()
    calls = []

    def operation(order):
        calls.append(order)
        raise StaleDataError("still stale")

    with pytest.raises(StaleDataError, match="still stale"):
        _commit_order_transaction(db, 17, operation)

    assert len(calls) == 2
    assert db.rollbacks == 2
    assert db.commits == 0
