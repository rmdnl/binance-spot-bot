from decimal import Decimal as D

from dca_bot.capital_state import allocated_quote
from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord


def test_allocated_quote_includes_active_deals_and_pending_buys(db_session):
    strategy = StrategyRecord(name="capital", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    deal = TestnetDealRecord(
        strategy_id=strategy.id, client_deal_id="capital-1", symbol="BTC/USDT", status="active",
        base_qty="1", quote_cost="100", average_entry="100", realized_pnl="0", dca_level=0, state_json="{}",
    )
    pending = ExchangeOrderRecord(
        strategy_id=strategy.id, symbol="ETH/USDT", side="buy", client_order_id="capital-buy-1",
        status="open", amount="2", price="10", filled="0", last_accounted_filled="0", remaining="2", raw_json="{}",
    )
    db_session.add_all([deal, pending])
    db_session.commit()
    assert allocated_quote(db_session) == D("120")


def test_filled_buy_is_not_counted_twice_as_pending(db_session):
    strategy = StrategyRecord(name="capital2", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    pending = ExchangeOrderRecord(
        strategy_id=strategy.id, symbol="ETH/USDT", side="buy", client_order_id="capital-buy-2",
        status="partially_filled", amount="2", price="10", filled="1", last_accounted_filled="1", remaining="1", raw_json="{}",
    )
    db_session.add(pending)
    db_session.commit()
    assert allocated_quote(db_session) == D("10")
