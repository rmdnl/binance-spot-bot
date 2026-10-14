from decimal import Decimal as D

from dca_bot.dca_sequence import DcaOrderSpec, get_or_create_dca_intent
from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord
from dca_bot.testnet_position_service import sync_order_fill


def make_strategy(session):
    strategy = StrategyRecord(name="testnet-lifecycle", config_json="{}", enabled=True)
    session.add(strategy)
    session.commit()
    return strategy


def test_entry_dca_and_restart_safe_fill_accounting(db_session):
    strategy = make_strategy(db_session)
    deal = TestnetDealRecord(
        strategy_id=strategy.id,
        client_deal_id="lifecycle-1",
        symbol="BTC/USDT",
        status="active",
        base_qty="0",
        quote_cost="0",
        average_entry=None,
        realized_pnl="0",
        dca_level=0,
        state_json="{}",
    )
    db_session.add(deal)
    db_session.commit()

    entry = ExchangeOrderRecord(
        strategy_id=strategy.id, deal_id=deal.id, symbol="BTC/USDT", side="buy",
        client_order_id="entry-1", status="filled", amount="1", price="100",
        filled="1", last_accounted_filled="0", remaining="0", average="100", raw_json="{}",
    )
    db_session.add(entry)
    db_session.commit()

    assert sync_order_fill(db_session, deal, entry) == D("1")
    assert D(deal.base_qty) == D("1")
    assert D(deal.average_entry) == D("100")
    assert sync_order_fill(db_session, deal, entry) == D("0")

    dca = get_or_create_dca_intent(
        db_session,
        DcaOrderSpec(deal.id, strategy.id, "BTC/USDT", "buy", D("1"), D("90"), 1),
    )
    db_session.commit()
    dca.filled = "1"
    dca.average = "90"
    dca.status = "filled"
    db_session.commit()

    assert sync_order_fill(db_session, deal, dca) == D("1")
    assert D(deal.base_qty) == D("2")
    assert D(deal.average_entry) == D("95")
    assert sync_order_fill(db_session, deal, dca) == D("0")
