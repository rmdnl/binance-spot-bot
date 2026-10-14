from decimal import Decimal as D

import pytest

from dca_bot.close_service import evaluate_take_profit, get_or_create_close_intent, mark_close_filled
from dca_bot.models import ExchangeOrderRecord, TestnetDealRecord


def make_deal(db_session, qty="1", avg="100"):
    deal = TestnetDealRecord(strategy_id=1, client_deal_id="deal-close-1", symbol="BTC/USDT", status="active", base_qty=qty, quote_cost=str(D(qty) * D(avg)), average_entry=avg, realized_pnl="0", dca_level=0, state_json="{}")
    db_session.add(deal)
    db_session.commit()
    return deal


def test_take_profit_requires_actual_position(db_session):
    deal = make_deal(db_session)
    decision = evaluate_take_profit(deal, D("101"), D("0.01"))
    assert decision.should_close
    assert decision.target_price == D("101")


def test_close_intent_is_idempotent(db_session):
    deal = make_deal(db_session)
    first = get_or_create_close_intent(db_session, deal, D("101"))
    db_session.commit()
    second = get_or_create_close_intent(db_session, deal, D("101"))
    assert first.id == second.id
    assert second.client_order_id == f"tp-{deal.id}"


def test_close_cannot_be_marked_before_position_is_zero(db_session):
    deal = make_deal(db_session)
    order = get_or_create_close_intent(db_session, deal, D("101"))
    order.status = "closed"
    order.filled = order.amount
    order.last_accounted_filled = order.amount
    db_session.commit()
    with pytest.raises(RuntimeError):
        mark_close_filled(db_session, deal, order)
