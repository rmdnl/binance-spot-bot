from decimal import Decimal as D

import pytest

from dca_bot.take_profit import TakeProfitSpec, get_or_create_take_profit_intent


def test_take_profit_intent_is_idempotent(db_session):
    spec = TakeProfitSpec(21, 1, "BTC/USDT", D("0.002"), D("51000"))
    first = get_or_create_take_profit_intent(db_session, spec)
    db_session.commit()
    second = get_or_create_take_profit_intent(db_session, spec)
    assert first.id == second.id
    assert second.client_order_id == "tp-21"


def test_take_profit_rejects_non_positive_values(db_session):
    with pytest.raises(ValueError):
        get_or_create_take_profit_intent(db_session, TakeProfitSpec(21, 1, "BTC/USDT", D("0"), D("51000")))
