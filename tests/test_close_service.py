from decimal import Decimal as D

import pytest

from dca_bot.close_service import evaluate_take_profit, get_or_create_close_intent, mark_close_filled
from dca_bot.models import TestnetDealRecord
from dca_bot.take_profit import TakeProfitSpec


def make_deal(db_session, qty="1", avg="100"):
    deal = TestnetDealRecord(
        strategy_id=1,
        client_deal_id=f"deal-close-{qty}-{avg}",
        symbol="BTC/USDT",
        status="active",
        base_qty=qty,
        quote_cost=str(D(qty) * D(avg)),
        average_entry=avg,
        realized_pnl="0",
        dca_level=0,
        state_json="{}",
    )
    db_session.add(deal)
    db_session.commit()
    return deal


def costs():
    return D("0.001"), D("0.001"), D("0.0005")


def test_take_profit_requires_actual_position(db_session):
    deal = make_deal(db_session)
    buy_fee, sell_fee, slippage = costs()
    decision = evaluate_take_profit(deal, D("100.35"), D("0.003"), buy_fee, sell_fee, slippage)
    assert not decision.should_close
    decision = evaluate_take_profit(deal, D("100.50"), D("0.003"), buy_fee, sell_fee, slippage)
    assert decision.should_close
    assert decision.target_price is not None
    assert decision.target_price > D("100.30")


def test_close_intent_is_idempotent(db_session):
    deal = make_deal(db_session)
    buy_fee, sell_fee, slippage = costs()
    spec = TakeProfitSpec(deal.id, deal.strategy_id, deal.symbol, D(deal.base_qty), D(deal.average_entry), D("0.003"), buy_fee, sell_fee, slippage)
    first = get_or_create_close_intent(db_session, deal, spec)
    db_session.commit()
    second = get_or_create_close_intent(db_session, deal, spec)
    assert first.id == second.id
    assert second.client_order_id == f"tp-{deal.id}"


def test_close_rejects_quantity_above_position(db_session):
    deal = make_deal(db_session)
    buy_fee, sell_fee, slippage = costs()
    spec = TakeProfitSpec(deal.id, deal.strategy_id, deal.symbol, D("2"), D("100"), D("0.003"), buy_fee, sell_fee, slippage)
    with pytest.raises(RuntimeError):
        get_or_create_close_intent(db_session, deal, spec)


def test_close_cannot_be_marked_before_position_is_zero(db_session):
    deal = make_deal(db_session)
    buy_fee, sell_fee, slippage = costs()
    spec = TakeProfitSpec(deal.id, deal.strategy_id, deal.symbol, D(deal.base_qty), D("100"), D("0.003"), buy_fee, sell_fee, slippage)
    order = get_or_create_close_intent(db_session, deal, spec)
    order.status = "closed"
    order.filled = order.amount
    order.last_accounted_filled = order.amount
    db_session.commit()
    with pytest.raises(RuntimeError):
        mark_close_filled(db_session, deal, order)
