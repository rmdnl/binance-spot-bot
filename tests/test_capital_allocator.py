from decimal import Decimal as D

import pytest

from dca_bot.capital_allocator import CapitalAllocator


def test_available_capital():
    allocator = CapitalAllocator(D("120"), D("50"))
    assert allocator.available(D("30")) == D("90")


def test_allocation_is_clamped_to_per_deal_limit():
    result = CapitalAllocator(D("120"), D("50")).allocate(D("80"), D("0"))
    assert result.allocated_quote == D("50")
    assert result.reason == "clamped_to_limits"


def test_allocation_is_clamped_to_global_remaining():
    result = CapitalAllocator(D("120"), D("50")).allocate(D("50"), D("100"))
    assert result.allocated_quote == D("20")
    assert result.available_quote == D("20")


def test_no_allocation_after_global_limit():
    result = CapitalAllocator(D("120"), D("50")).allocate(D("10"), D("120"))
    assert result.allocated_quote == D("0")
    assert result.reason == "global_limit_reached"


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        CapitalAllocator(D("0"), D("50"))
