from decimal import Decimal as D

import pytest

from dca_bot.dca_math import build_dca_summary


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
