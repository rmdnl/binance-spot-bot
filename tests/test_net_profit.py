from decimal import Decimal as D

import pytest

from dca_bot.net_profit import required_exit_price


def test_exit_price_covers_fees_slippage_and_target():
    price = required_exit_price(D("100"), D("0.003"), D("0.001"), D("0.001"), D("0.0005"))
    assert price > D("100.5")


def test_invalid_costs_are_rejected():
    with pytest.raises(ValueError):
        required_exit_price(D("100"), D("0.003"), D("0.001"), D("0.7"), D("0.4"))
