from decimal import Decimal as D

import pytest

from dca_bot.paper_engine import (
    deserialize_paper_deal, serialize_paper_deal, start_paper_deal, update_paper_deal,
)
from dca_bot.strategy import StrategyConfig


def config(**overrides):
    values = {
        "symbols": ["BTC/USDT"],
        "base_order_quote": 10,
        "safety_order_quote": 10,
        "max_safety_orders": 2,
        "initial_deviation_pct": 1,
        "deviation_scale": 1,
        "safety_order_volume_scale": 1,
        "take_profit_pct": 1,
        "max_capital_per_deal": 100,
        "max_total_allocation": 300,
        "fee_rate_pct_per_side": 0.1,
        "slippage_pct_per_side": 0,
    }
    values.update(overrides)
    return StrategyConfig(**values)


def test_paper_deal_fills_base_order_and_tracks_average_entry():
    deal = start_paper_deal(config(), "BTCUSDT", D("100"))
    assert deal.status == "active"
    assert deal.quote_spent == D("10")
    assert deal.base_quantity == D("0.1")
    assert deal.average_entry_price == D("100")
    assert deal.events[0].event_type == "base_order_filled"


def test_paper_deal_fills_safety_orders_as_price_crosses_levels():
    deal = start_paper_deal(config(), "BTC/USDT", D("100"))
    events = update_paper_deal(deal, D("97"))
    assert [e.event_type for e in events] == ["safety_order_filled", "safety_order_filled"]
    assert deal.safety_orders_filled == 2
    assert deal.quote_spent == D("30")
    assert deal.average_entry_price < D("100")


def test_paper_deal_closes_at_take_profit_and_accounts_for_fees():
    deal = start_paper_deal(config(max_safety_orders=0), "BTC/USDT", D("100"))
    events = update_paper_deal(deal, D("102"))
    assert events[-1].event_type == "deal_closed"
    assert deal.status == "closed"
    assert deal.close_reason == "take_profit"
    assert deal.realized_pnl_quote > 0
    assert deal.realized_pnl_quote < D("0.2")


def test_paper_deal_stop_loss_closes_before_take_profit():
    deal = start_paper_deal(
        config(max_safety_orders=0, stop_loss_enabled=True, stop_loss_pct=5), "BTC/USDT", D("100")
    )
    events = update_paper_deal(deal, D("94"))
    assert events[-1].event_type == "deal_closed"
    assert deal.close_reason == "stop_loss_or_breakeven"
    assert deal.realized_pnl_quote < 0


def test_breakeven_moves_stop_after_activation():
    deal = start_paper_deal(
        config(max_safety_orders=0, take_profit_pct=5, stop_loss_enabled=True, stop_loss_pct=20,
               move_stop_to_breakeven_enabled=True, breakeven_activation_pct=1),
        "BTC/USDT", D("100")
    )
    assert update_paper_deal(deal, D("102")) == []
    assert deal.breakeven_active is True
    events = update_paper_deal(deal, D("99.9"))
    assert events[-1].event_type == "deal_closed"
    assert deal.close_reason == "stop_loss_or_breakeven"


def test_trailing_take_profit_activates_and_closes_on_pullback():
    deal = start_paper_deal(
        config(max_safety_orders=0, trailing_take_profit_enabled=True,
               trailing_activation_pct=1, trailing_callback_pct=0.5),
        "BTC/USDT", D("100")
    )
    assert update_paper_deal(deal, D("102")) == []
    assert deal.trailing_active is True
    events = update_paper_deal(deal, D("101.4"))
    assert events[-1].event_type == "deal_closed"
    assert deal.close_reason == "trailing_take_profit"


def test_closed_paper_deal_is_idempotent():
    deal = start_paper_deal(config(max_safety_orders=0), "BTC/USDT", D("100"))
    update_paper_deal(deal, D("102"))
    assert update_paper_deal(deal, D("90")) == []
    assert deal.status == "closed"


def test_paper_deal_rejects_pair_not_in_strategy():
    with pytest.raises(ValueError, match="not included"):
        start_paper_deal(config(), "ETH/USDT", D("100"))



def test_paper_deal_state_round_trips_for_restart_recovery():
    deal = start_paper_deal(config(), "BTC/USDT", D("100"))
    update_paper_deal(deal, D("99"))
    restored = deserialize_paper_deal(serialize_paper_deal(deal))
    assert restored.id == deal.id
    assert restored.status == deal.status
    assert restored.quote_spent == deal.quote_spent
    assert restored.base_quantity == deal.base_quantity
    assert restored.safety_orders_filled == deal.safety_orders_filled
    assert restored.average_entry_price == deal.average_entry_price
    assert [event.event_type for event in restored.events] == [event.event_type for event in deal.events]



def test_max_deal_duration_closes_an_expired_paper_deal():
    import time

    deal = start_paper_deal(
        config(max_safety_orders=0, max_deal_duration_hours=1), "BTC/USDT", D("100")
    )
    deal.started_at_epoch = time.time() - 3601
    events = update_paper_deal(deal, D("99"))
    assert events[-1].event_type == "deal_closed"
    assert deal.close_reason == "max_deal_duration"
    assert deal.status == "closed"
