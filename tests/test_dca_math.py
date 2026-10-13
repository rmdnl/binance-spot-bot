from decimal import Decimal as D

import pytest

from dca_bot.dca_math import build_dca_summary
from dca_bot.risk import reserved_capital_quote, strategy_risk_status


def test_even_safety_order_ladder_and_capital():
    summary = build_dca_summary(
        base_price=D("100"), base_order_quote=D("10"), safety_order_quote=D("10"),
        safety_order_count=3, initial_deviation_pct=D("1"),
        deviation_scale=D("1"), safety_volume_scale=D("1"),
    )
    assert [level.deviation_pct for level in summary.safety_orders] == [D("1"), D("2"), D("3")]
    assert [level.price for level in summary.safety_orders] == [D("99"), D("98"), D("97")]
    assert summary.total_quote_required == D("40.00000000")
    assert summary.deepest_deviation_pct == D("3")


def test_scaled_deviation_matches_arithmetic_cumulative_ladder():
    summary = build_dca_summary(
        base_price=D("100"), base_order_quote=D("10"), safety_order_quote=D("10"),
        safety_order_count=4, initial_deviation_pct=D("1"),
        deviation_scale=D("2"), safety_volume_scale=D("1.5"),
    )
    assert [level.deviation_pct for level in summary.safety_orders] == [
        D("1"), D("3"), D("7"), D("15")
    ]
    assert [level.price for level in summary.safety_orders] == [
        D("99"), D("97"), D("93"), D("85")
    ]
    assert summary.total_quote_required == D("91.25000000")


def test_average_entry_uses_coin_quantity_weighting():
    summary = build_dca_summary(
        base_price=D("100"), base_order_quote=D("100"), safety_order_quote=D("100"),
        safety_order_count=1, initial_deviation_pct=D("10"),
    )
    expected = D("200") / (D("1") + D("100") / D("90"))
    assert summary.average_entry_price == expected
    assert summary.average_entry_price < D("100")


def test_rejects_ladder_below_zero_price():
    with pytest.raises(ValueError, match="zero or negative"):
        build_dca_summary(
            base_price=D("100"), base_order_quote=D("10"), safety_order_quote=D("10"),
            safety_order_count=3, initial_deviation_pct=D("40"),
            deviation_scale=D("2"),
        )


def test_rejects_invalid_order_count():
    with pytest.raises(ValueError, match="between 0 and 30"):
        build_dca_summary(
            base_price=D("100"), base_order_quote=D("10"), safety_order_count=31,
            safety_order_quote=D("10"), initial_deviation_pct=D("1"),
        )



def test_order_averaging_applies_volume_multiplier_to_first_safety_order():
    position = build_dca_summary(
        base_price=D("100"), base_order_quote=D("10"), safety_order_quote=D("10"),
        safety_order_count=2, initial_deviation_pct=D("1"), deviation_scale=D("1"),
        safety_volume_scale=D("1.5"), dca_mode="position_averaging",
    )
    order = build_dca_summary(
        base_price=D("100"), base_order_quote=D("10"), safety_order_quote=D("10"),
        safety_order_count=2, initial_deviation_pct=D("1"), deviation_scale=D("1"),
        safety_volume_scale=D("1.5"), dca_mode="order_averaging",
    )
    assert [x.quote_amount for x in position.safety_orders] == [D("10"), D("15")]
    assert [x.quote_amount for x in order.safety_orders] == [D("15"), D("22.5")]
    assert order.total_quote_required > position.total_quote_required


from types import SimpleNamespace


def _active_deal_with_cap(cap):
    return SimpleNamespace(config=SimpleNamespace(max_capital_per_deal=cap))


def test_reserved_capital_uses_full_cap_for_each_active_deal():
    assert reserved_capital_quote([_active_deal_with_cap(100), _active_deal_with_cap(200)]) == D("300")


def test_reserved_capital_is_zero_without_active_deals():
    assert reserved_capital_quote([]) == D("0")


@pytest.mark.parametrize("cap", ["NaN", "Infinity", "-1"])
def test_reserved_capital_rejects_invalid_reservations(cap):
    with pytest.raises(ValueError, match="invalid capital reservation"):
        reserved_capital_quote([_active_deal_with_cap(cap)])



def _risk_test_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from dca_bot.db import Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def _risk_test_strategy(**overrides):
    from dca_bot.strategy import StrategyConfig
    values = {
        "symbols": ["BTC/USDT"],
        "base_order_quote": 100,
        "safety_order_quote": 10,
        "max_safety_orders": 0,
        "max_capital_per_deal": 200,
        "max_total_allocation": 300,
        "reserve_balance": 20,
        "daily_loss_limit_quote": 15,
        "max_equity_drawdown_pct": 5,
        "stop_loss_enabled": True,
        "stop_loss_pct": 5,
    }
    values.update(overrides)
    return StrategyConfig(**values)


def _insert_closed_loss(session, config, pnl="-20"):
    from datetime import datetime, timezone
    from dca_bot.models import PaperDealRecord, StrategyRecord
    from dca_bot.paper_engine import serialize_paper_deal, start_paper_deal
    strategy = StrategyRecord(name=config.name, config_json=config.model_dump_json(), enabled=True)
    session.add(strategy)
    session.flush()
    deal = start_paper_deal(config, "BTC/USDT", D("100"))
    deal.status = "closed"
    deal.exit_price = D("80")
    deal.close_reason = "test_loss"
    deal.realized_pnl_quote = D(pnl)
    row = PaperDealRecord(
        id=deal.id,
        strategy_id=strategy.id,
        symbol=deal.symbol,
        status="closed",
        state_json=serialize_paper_deal(deal),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return strategy


def test_strategy_risk_guard_latches_equity_drawdown():
    engine, session = _risk_test_session()
    try:
        from dca_bot.models import StrategyRiskState
        strategy = _insert_closed_loss(session, _risk_test_strategy(daily_loss_limit_quote=100))
        status = strategy_risk_status(session, strategy)
        assert status["blocked"] is True
        assert status["drawdown_tripped"] is True
        assert D(status["drawdown_pct"]) >= D("5")
        assert session.get(StrategyRiskState, strategy.id).drawdown_tripped is True
    finally:
        session.close()
        engine.dispose()


def test_strategy_risk_guard_blocks_daily_loss_without_drawdown_trip():
    engine, session = _risk_test_session()
    try:
        strategy = _insert_closed_loss(
            session,
            _risk_test_strategy(daily_loss_limit_quote=15, max_equity_drawdown_pct=10),
        )
        status = strategy_risk_status(session, strategy)
        assert status["blocked"] is True
        assert status["daily_loss_tripped"] is True
        assert status["drawdown_tripped"] is False
    finally:
        session.close()
        engine.dispose()
