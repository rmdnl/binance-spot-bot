from decimal import Decimal

from dca_bot.risk import quote_asset, reserved_capital_by_quote, reserved_capital_quote


class DealConfig:
    def __init__(self, cap):
        self.max_capital_per_deal = cap


class Deal:
    def __init__(self, symbol, cap):
        self.symbol = symbol
        self.config = DealConfig(cap)


def test_quote_asset_is_derived_from_symbol():
    assert quote_asset("BTC/USDT") == "USDT"
    assert quote_asset("ETH/BTC") == "BTC"
    assert quote_asset("SOL/USDC") == "USDC"


def test_capital_is_grouped_without_cross_currency_addition():
    deals = [
        Deal("BTC/USDT", "50"),
        Deal("ETH/USDT", "25"),
        Deal("ETH/BTC", "0.01"),
        Deal("SOL/USDC", "100"),
    ]
    assert reserved_capital_by_quote(deals) == {
        "USDT": Decimal("75"),
        "BTC": Decimal("0.01"),
        "USDC": Decimal("100"),
    }


def test_legacy_single_quote_helper_remains_safe_for_one_bucket():
    deals = [Deal("BTC/USDT", "50"), Deal("ETH/USDT", "25")]
    assert reserved_capital_quote(deals) == Decimal("75")


def test_single_quote_helper_rejects_mixed_buckets():
    deals = [Deal("BTC/USDT", "50"), Deal("ETH/BTC", "0.01")]
    try:
        reserved_capital_quote(deals)
    except ValueError as exc:
        assert "multiple quote currencies" in str(exc)
    else:
        raise AssertionError("mixed quote currencies must never be collapsed into one Decimal")
