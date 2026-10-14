from decimal import Decimal as D

import pytest

from dca_bot.close_service import get_or_create_close_intent, mark_close_filled
from dca_bot.dca_sequence import DcaOrderSpec, get_or_create_dca_intent
from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord
from dca_bot.net_profit import required_exit_price
from dca_bot.take_profit import TakeProfitSpec
from dca_bot.testnet_position_service import sync_order_fill


def make_strategy(session):
    strategy = StrategyRecord(name="testnet-lifecycle", config_json="{}", enabled=True)
    session.add(strategy)
    session.commit()
    return strategy


def test_entry_dca_tp_and_restart_safe_lifecycle(db_session):
    strategy = make_strategy(db_session)
    deal = TestnetDealRecord(
        strategy_id=strategy.id, client_deal_id="lifecycle-1", symbol="BTC/USDT", status="active",
        base_qty="0", quote_cost="0", average_entry=None, realized_pnl="0", dca_level=0, state_json="{}",
    )
    db_session.add(deal)
    db_session.commit()

    entry = ExchangeOrderRecord(
        strategy_id=strategy.id, deal_id=deal.id, symbol="BTC/USDT", side="buy",
        client_order_id="entry-1", status="filled", amount="1", price="100", filled="1",
        last_accounted_filled="0", remaining="0", average="100", raw_json="{}",
    )
    db_session.add(entry)
    db_session.commit()
    assert sync_order_fill(db_session, deal, entry) == D("1")
    assert sync_order_fill(db_session, deal, entry) == D("0")

    dca = get_or_create_dca_intent(db_session, DcaOrderSpec(deal.id, strategy.id, "BTC/USDT", "buy", D("1"), D("90"), 1))
    db_session.commit()
    dca.filled, dca.average, dca.status = "1", "90", "filled"
    db_session.commit()
    assert sync_order_fill(db_session, deal, dca) == D("1")
    assert sync_order_fill(db_session, deal, dca) == D("0")
    assert D(deal.base_qty) == D("2")
    assert D(deal.average_entry) == D("95")

    buy_fee, sell_fee, slippage, target_net = D("0.001"), D("0.001"), D("0.0005"), D("0.003")
    target = required_exit_price(D("95"), target_net, buy_fee, sell_fee, slippage)
    spec = TakeProfitSpec(deal.id, strategy.id, deal.symbol, D("2"), D("95"), target_net, buy_fee, sell_fee, slippage)
    assert spec.target_price == target
    tp = get_or_create_close_intent(db_session, deal, spec)
    db_session.commit()
    assert tp.client_order_id == f"tp-{deal.id}"

    tp.filled, tp.average, tp.status = "2", str(target), "filled"
    db_session.commit()
    assert sync_order_fill(db_session, deal, tp) == D("2")
    assert D(deal.base_qty) == D("0")
    assert D(deal.realized_pnl) > 0
    assert deal.status == "closed"
    assert sync_order_fill(db_session, deal, tp) == D("0")
    mark_close_filled(db_session, deal, tp)
    assert deal.status == "closed"


def test_sell_fill_cannot_close_until_accounted(db_session):
    strategy = make_strategy(db_session)
    deal = TestnetDealRecord(
        strategy_id=strategy.id, client_deal_id="lifecycle-2", symbol="BTC/USDT", status="active",
        base_qty="1", quote_cost="100", average_entry="100", realized_pnl="0", dca_level=0, state_json="{}",
    )
    db_session.add(deal)
    db_session.commit()
    order = ExchangeOrderRecord(
        strategy_id=strategy.id, deal_id=deal.id, symbol="BTC/USDT", side="sell",
        client_order_id="tp-2", status="filled", amount="1", price="101", filled="1",
        last_accounted_filled="0", remaining="0", average="101", raw_json="{}",
    )
    db_session.add(order)
    db_session.commit()
    with pytest.raises(RuntimeError):
        mark_close_filled(db_session, deal, order)
