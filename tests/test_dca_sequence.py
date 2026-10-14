from decimal import Decimal as D

from dca_bot.dca_sequence import DcaOrderSpec, get_or_create_dca_intent, next_dca_level


def test_dca_intent_is_idempotent(db_session):
    spec = DcaOrderSpec(7, 1, "BTC/USDT", "buy", D("0.001"), D("49000"), 1)
    first = get_or_create_dca_intent(db_session, spec)
    db_session.commit()
    second = get_or_create_dca_intent(db_session, spec)
    assert first.id == second.id
    assert second.client_order_id == "dca-7-1"


def test_next_dca_level_is_derived_from_persisted_orders(db_session):
    for level in (1, 2, 3):
        get_or_create_dca_intent(
            db_session,
            DcaOrderSpec(9, 1, "BTC/USDT", "buy", D("0.001"), D("49000"), level),
        )
    db_session.commit()
    assert next_dca_level(db_session, 9) == 4


def test_dca_identity_collision_is_rejected(db_session):
    spec = DcaOrderSpec(11, 1, "BTC/USDT", "buy", D("0.001"), D("49000"), 1)
    get_or_create_dca_intent(db_session, spec)
    db_session.commit()
    conflicting = DcaOrderSpec(11, 1, "ETH/USDT", "buy", D("0.001"), D("49000"), 1)
    try:
        get_or_create_dca_intent(db_session, conflicting)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected client order id collision")
