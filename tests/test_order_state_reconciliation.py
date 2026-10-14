from decimal import Decimal as D
from types import SimpleNamespace

from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord
from dca_bot.reconciliation import reconcile_orders


class FakeAdapter:
    def __init__(self, result):
        self.result = result

    def fetch_order(self, symbol, order_id):
        return self.result

    def fetch_order_by_client_id(self, symbol, client_order_id):
        return [self.result]


def make_order(db_session, status="open", filled="0"):
    strategy = StrategyRecord(name="state", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    deal = TestnetDealRecord(strategy_id=strategy.id, client_deal_id="state-deal", symbol="BTC/USDT", status="pending", base_qty="0", quote_cost="0", average_entry=None, realized_pnl="0", dca_level=0, state_json="{}")
    db_session.add(deal)
    db_session.flush()
    order = ExchangeOrderRecord(strategy_id=strategy.id, deal_id=deal.id, symbol="BTC/USDT", side="buy", client_order_id="state-order", status=status, amount="1", price="100", filled=filled, last_accounted_filled=filled, remaining=str(D("1") - D(filled)), raw_json="{}")
    db_session.add(order)
    db_session.commit()
    return strategy, order


def test_exchange_closed_maps_to_local_filled(db_session):
    strategy, order = make_order(db_session)
    remote = SimpleNamespace(order_id="42", client_order_id="state-order", symbol="BTC/USDT", side="buy", status="closed", amount=D("1"), filled=D("1"), remaining=D("0"), average=D("101"), price=D("100"), raw={"status": "closed"})
    result = reconcile_orders(db_session, FakeAdapter(remote), strategy_id=strategy.id)
    db_session.refresh(order)
    assert result["updated"] == 1
    assert order.status == "filled"
    assert order.exchange_order_id == "42"


def test_partial_fill_maps_to_local_partial(db_session):
    strategy, order = make_order(db_session)
    remote = SimpleNamespace(order_id="43", client_order_id="state-order", symbol="BTC/USDT", side="buy", status="open", amount=D("1"), filled=D("0.4"), remaining=D("0.6"), average=D("100"), price=D("100"), raw={"status": "open"})
    result = reconcile_orders(db_session, FakeAdapter(remote), strategy_id=strategy.id)
    db_session.refresh(order)
    assert result["updated"] == 1
    assert order.status == "partially_filled"
    assert D(order.filled) == D("0.4")


def test_missing_remote_order_never_resubmits(db_session):
    strategy, order = make_order(db_session, status="intent")

    class MissingAdapter(FakeAdapter):
        def fetch_order_by_client_id(self, symbol, client_order_id):
            return []

    result = reconcile_orders(db_session, MissingAdapter(None), strategy_id=strategy.id)
    db_session.refresh(order)
    assert result["query_required"]
    assert order.status == "intent"
