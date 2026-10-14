from decimal import Decimal as D

import pytest

from dca_bot.fill_state import apply_entry_fill
from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord


def make_order(db_session):
    strategy = StrategyRecord(name="fill", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    deal = TestnetDealRecord(strategy_id=strategy.id, client_deal_id="fill-deal", symbol="BTC/USDT", status="pending", base_qty="0", quote_cost="0", average_entry=None, realized_pnl="0", dca_level=0, state_json="{}")
    db_session.add(deal)
    db_session.flush()
    order = ExchangeOrderRecord(strategy_id=strategy.id, deal_id=deal.id, symbol="BTC/USDT", side="buy", client_order_id="fill-order", status="open", amount="1", price="100", filled="0", last_accounted_filled="0", remaining="1", raw_json="{}")
    db_session.add(order)
    db_session.commit()
    return order


def test_partial_then_full_fill_updates_only_delta(db_session):
    order = make_order(db_session)
    deal = apply_entry_fill(db_session, order, D("0.4"), D("100"))
    assert D(deal.base_qty) == D("0.4")
    assert D(deal.quote_cost) == D("40")
    deal = apply_entry_fill(db_session, order, D("1"), D("101"))
    assert D(deal.base_qty) == D("1")
    assert D(deal.quote_cost) == D("100.6")
    assert deal.status == "active"


def test_same_fill_is_idempotent(db_session):
    order = make_order(db_session)
    apply_entry_fill(db_session, order, D("0.5"), D("100"))
    deal = apply_entry_fill(db_session, order, D("0.5"), D("100"))
    assert D(deal.base_qty) == D("0.5")
    assert D(deal.quote_cost) == D("50")


def test_fill_cannot_go_backward(db_session):
    order = make_order(db_session)
    apply_entry_fill(db_session, order, D("0.5"), D("100"))
    with pytest.raises(ValueError, match="moved backwards"):
        apply_entry_fill(db_session, order, D("0.4"), D("100"))
