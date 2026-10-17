from decimal import Decimal as D

import pytest

from spot_bot.profitability import calculate_grid_economics, enforce_min_net_profit


def test_grid_economics_accounts_for_both_fees_and_two_sided_slippage():
    economics = calculate_grid_economics(
        buy_price=D("100"), sell_price=D("101"), amount=D("2"),
        maker_fee_pct=D("0.10"), slippage_pct=D("0.05"),
    )
    assert economics.gross_quote == D("2")
    assert economics.buy_fee_quote == D("0.20")
    assert economics.sell_fee_quote == D("0.202")
    assert economics.slippage_quote == D("0.201")
    assert economics.net_quote == D("1.397")
    assert economics.net_pct == D("0.6985")


def test_minimum_profit_guard_allows_exact_boundary():
    economics = calculate_grid_economics(D("100"), D("101"), D("1"), D("0.10"), D("0.05"))
    enforce_min_net_profit(economics, economics.net_pct)


def test_minimum_profit_guard_rejects_below_threshold():
    economics = calculate_grid_economics(D("100"), D("101"), D("1"), D("0.10"), D("0.05"))
    with pytest.raises(ValueError, match="below minimum"):
        enforce_min_net_profit(economics, D("0.70"))


@pytest.mark.parametrize("values", [
    (D("0"), D("101"), D("1")),
    (D("101"), D("100"), D("1")),
    (D("100"), D("101"), D("0")),
])
def test_invalid_economics_inputs_are_rejected(values):
    with pytest.raises(ValueError):
        calculate_grid_economics(*values, maker_fee_pct=D("0.10"), slippage_pct=D("0.05"))


def test_negative_fee_or_slippage_is_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_grid_economics(D("100"), D("101"), D("1"), D("-0.01"), D("0.05"))
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_grid_economics(D("100"), D("101"), D("1"), D("0.10"), D("-0.01"))
