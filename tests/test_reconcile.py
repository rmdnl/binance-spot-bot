from decimal import Decimal as D
from types import SimpleNamespace

from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord
from dca_bot.reconciliation import reconcile_orders


class FakeAdapter:
    def __init__(self, result):
        self.result = result

    def fetch_order_by_client_id(self, symbol, client_order_id):
        return [self.result]

    def fetch_order(self, symbol, order_id):
        return self.result


def make_order(db_session):
    strategy = StrategyRecord(name="reconcile", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    deal = TestnetDealRecord(strategy_id=strategy.id, client_deal_id="reconcile-deal", symbol="BTC/USDT", status="pending", base_qty="0", quote_cost="0", average_entry=None, realized_pnl="0", dca_level=0, state_json="{}")
    db_session.add(deal)
    db_session.flush()
    order = ExchangeOrderRecord(strategy_id=strategy.id, deal_id=deal.id, symbol="BTC/USDT", side="buy", client_order_id="reconcile-order", status="open", amount="1", price="100", filled="0", last_accounted_filled="0", remaining="1", raw_json="{}")
    db_session.add(order)
    db_session.commit()
    return strategy, order


def test_reconcile_applies_fill(db_session):
    strategy, order = make_order(db_session)
    result = SimpleNamespace(
        order_id="123", client_order_id="reconcile-order", symbol="BTC/USDT", side="buy",
        status="filled", amount=D("1"), price=D("100"), filled=D("1"), remaining=D("0"),
        average=D("101"), raw={"status": "FILLED"},
    )
    stats = reconcile_orders(db_session, FakeAdapter(result), strategy_id=strategy.id)
    db_session.refresh(order)
    assert stats["updated"] == 1
    assert stats["fills_accounted"] == 1
    assert order.exchange_order_id == "123"
    assert order.status == "filled"
    assert D(order.filled) == D("1")
    assert order.deal_id is not None
    deal = db_session.get(TestnetDealRecord, order.deal_id)
    assert deal.status == "active"
    assert D(deal.base_qty) == D("1")
    assert D(deal.quote_cost) == D("101")
