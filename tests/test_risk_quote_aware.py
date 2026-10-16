from decimal import Decimal as D
import pytest
from types import SimpleNamespace

from dca_bot.risk import quote_asset, reserved_capital_by_quote, reserved_capital_quote, RiskLimits, RiskSnapshot, check_risk


def test_quote_asset_requires_explicit_pair():
    assert quote_asset("ETH/BTC") == "BTC"
    with pytest.raises(ValueError):
        quote_asset("ETHBTC")


def test_reservations_are_never_mixed_across_quotes():
    deals = [
        SimpleNamespace(symbol="BTC/USDT", reserved_quote="100", state_json={}),
        SimpleNamespace(symbol="ETH/BTC", reserved_quote="0.01", state_json={}),
        SimpleNamespace(symbol="SOL/USDT", reserved_quote="25", state_json={}),
    ]
    assert reserved_capital_by_quote(deals) == {"USDT": D("125"), "BTC": D("0.01")}
    with pytest.raises(ValueError, match="different quote currencies"):
        reserved_capital_quote(deals)


def test_invalid_reservation_fails_closed():
    deal = SimpleNamespace(symbol="BTC/USDT", reserved_quote="-1", state_json={})
    with pytest.raises(ValueError):
        reserved_capital_by_quote([deal])


def test_insufficient_balance_blocks_trade():
    limits = RiskLimits(max_capital=D("500"))
    snapshot = RiskSnapshot(D("1000"), D("49"), D("0"), D("0"), 0, False)
    with pytest.raises(RuntimeError, match="Insufficient available"):
        check_risk(limits, snapshot, D("50"))
