from dca_bot.intent_queue import pending_entry_intents
from dca_bot.models import ExchangeOrderRecord, StrategyRecord


def test_pending_entry_intents_returns_only_live_buys(db_session):
    strategy = StrategyRecord(name="queue", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    rows = [
        ExchangeOrderRecord(strategy_id=strategy.id, symbol="BTC/USDT", side="buy", client_order_id="q1", status="intent", amount="1", price="100", filled="0", remaining="1", raw_json="{}"),
        ExchangeOrderRecord(strategy_id=strategy.id, symbol="ETH/USDT", side="buy", client_order_id="q2", status="open", amount="1", price="100", filled="0", remaining="1", raw_json="{}"),
        ExchangeOrderRecord(strategy_id=strategy.id, symbol="SOL/USDT", side="sell", client_order_id="q3", status="open", amount="1", price="100", filled="0", remaining="1", raw_json="{}"),
        ExchangeOrderRecord(strategy_id=strategy.id, symbol="BNB/USDT", side="buy", client_order_id="q4", status="filled", amount="1", price="100", filled="1", remaining="0", raw_json="{}"),
    ]
    db_session.add_all(rows)
    db_session.commit()
    assert [r.client_order_id for r in pending_entry_intents(db_session, strategy.id)] == ["q1", "q2"]
