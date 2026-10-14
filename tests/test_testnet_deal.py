from decimal import Decimal
from types import SimpleNamespace

from dca_bot.execution import ExecutionError
from dca_bot.testnet_deal import TestnetDealIntent, TestnetDealService


def make_adapter(result=None, error=None, matches=None):
    class Adapter:
        def place_limit(self, intent):
            if error:
                raise error
            return result

        def fetch_order(self, symbol, order_id):
            return result

        def fetch_order_by_client_id(self, symbol, client_order_id):
            return matches if matches is not None else []

    return Adapter()


def order_result(status="open", order_id="ex-1", client_id="cid-1", symbol="BTC/USDT"):
    return SimpleNamespace(
        order_id=order_id,
        client_order_id=client_id,
        symbol=symbol,
        side="buy",
        status=status,
        amount=Decimal("0.001"),
        filled=Decimal("0"),
        remaining=Decimal("0.001"),
        average=None,
        price=Decimal("50000"),
        raw={},
    )


def test_create_entry_intent_is_idempotent(db_session):
    service = TestnetDealService(make_adapter())
    intent = TestnetDealIntent(1, "BTC/USDT", "buy", Decimal("0.001"), Decimal("50000"), "cid-1")
    first = service.create_entry_intent(db_session, intent)
    second = service.create_entry_intent(db_session, intent)
    assert first.id == second.id


def test_submit_entry_persists_exchange_order(db_session):
    service = TestnetDealService(make_adapter(result=order_result()))
    intent = TestnetDealIntent(1, "BTC/USDT", "buy", Decimal("0.001"), Decimal("50000"), "cid-1")
    row = service.create_entry_intent(db_session, intent)
    result = service.submit_entry(db_session, row)
    assert result.order_id == "ex-1"
    assert row.exchange_order_id == "ex-1"
    assert row.status == "open"


def test_ambiguous_submission_becomes_unknown(db_session):
    service = TestnetDealService(make_adapter(error=ExecutionError("timeout")))
    intent = TestnetDealIntent(1, "BTC/USDT", "buy", Decimal("0.001"), Decimal("50000"), "cid-2")
    row = service.create_entry_intent(db_session, intent)
    try:
        service.submit_entry(db_session, row)
    except ExecutionError:
        pass
    assert row.status == "unknown"


def test_unknown_order_is_reconciled_by_client_id(db_session):
    remote = order_result()
    service = TestnetDealService(make_adapter(matches=[remote]))
    intent = TestnetDealIntent(1, "BTC/USDT", "buy", Decimal("0.001"), Decimal("50000"), "cid-1")
    row = service.create_entry_intent(db_session, intent)
    row.status = "unknown"
    db_session.commit()
    result = service.reconcile_entry(db_session, row)
    assert result["status"] == "open"
    assert row.exchange_order_id == "ex-1"


def test_duplicate_client_id_match_halts(db_session):
    remote = order_result()
    service = TestnetDealService(make_adapter(matches=[remote, remote]))
    intent = TestnetDealIntent(1, "BTC/USDT", "buy", Decimal("0.001"), Decimal("50000"), "cid-3")
    row = service.create_entry_intent(db_session, intent)
    row.status = "unknown"
    db_session.commit()
    result = service.reconcile_entry(db_session, row)
    assert result["status"] == "halt"
    assert row.status == "halt"
