from decimal import Decimal as D

import pytest

from dca_bot.take_profit import TakeProfitSpec, get_or_create_take_profit_intent


def spec(quantity=D("0.002")):
    return TakeProfitSpec(
        21, 1, "BTC/USDT", quantity, D("51000"), D("0.003"), D("0.001"), D("0.001"), D("0.0005")
    )


def test_take_profit_intent_is_idempotent(db_session):
    first = get_or_create_take_profit_intent(db_session, spec())
    db_session.commit()
    second = get_or_create_take_profit_intent(db_session, spec())
    assert first.id == second.id
    assert second.client_order_id == "tp-21"


def test_take_profit_rejects_non_positive_values(db_session):
    with pytest.raises(ValueError):
        get_or_create_take_profit_intent(db_session, spec(D("0")))
