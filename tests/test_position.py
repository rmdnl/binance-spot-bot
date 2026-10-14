from decimal import Decimal as D

import pytest

from dca_bot.position import Position


def test_average_entry_uses_actual_buy_fills():
    p = Position("BTC/USDT")
    p.apply_buy_fill(D("1"), D("100"))
    p.apply_buy_fill(D("1"), D("80"))
    assert p.base_qty == D("2")
    assert p.average_entry == D("90")


def test_sell_realizes_profit_and_reduces_cost_basis():
    p = Position("BTC/USDT")
    p.apply_buy_fill(D("2"), D("100"))
    realized = p.apply_sell_fill(D("1"), D("120"))
    assert realized == D("20")
    assert p.base_qty == D("1")
    assert p.average_entry == D("100")


def test_sell_cannot_overdraw_position():
    p = Position("BTC/USDT")
    p.apply_buy_fill(D("1"), D("100"))
    with pytest.raises(ValueError):
        p.apply_sell_fill(D("1.1"), D("120"))


def test_filled_quantity_must_not_move_backwards():
    p = Position("BTC/USDT")
    with pytest.raises(ValueError):
        p.apply_order_delta("buy", D("1"), D("0.9"), D("100"))


def test_order_delta_only_applies_new_fill():
    p = Position("BTC/USDT")
    assert p.apply_order_delta("buy", D("0"), D("0.4"), D("100")) == D("0")
    assert p.apply_order_delta("buy", D("0.4"), D("0.4"), D("100")) == D("0")
    assert p.base_qty == D("0.4")
