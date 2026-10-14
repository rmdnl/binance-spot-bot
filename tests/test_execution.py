from decimal import Decimal as D

import pytest

from dca_bot.execution import (
    BinanceSpotTestnetAdapter,
    ExecutionSafetyError,
    OrderIntent,
    _floor_step,
)


def test_floor_step_is_decimal_safe():
    assert _floor_step(D("1.239"), D("0.01")) == D("1.23")
    assert _floor_step(D("0.123456"), D("0.001")) == D("0.123")


def test_adapter_refuses_non_testnet():
    with pytest.raises(ExecutionSafetyError, match="only permits Spot Testnet"):
        BinanceSpotTestnetAdapter("key", "secret", testnet=False)


def test_adapter_refuses_missing_credentials():
    with pytest.raises(ExecutionSafetyError, match="credentials are required"):
        BinanceSpotTestnetAdapter("", "", testnet=True)


def test_normalization_rejects_invalid_intent_without_exchange_call(monkeypatch):
    adapter = object.__new__(BinanceSpotTestnetAdapter)
    with pytest.raises(ValueError, match="amount must be positive"):
        adapter.normalize_limit(OrderIntent("BTC/USDT", "buy", D("0"), D("100")))


def test_normalization_rejects_missing_price():
    adapter = object.__new__(BinanceSpotTestnetAdapter)
    with pytest.raises(ValueError, match="price must be positive"):
        adapter.normalize_limit(OrderIntent("BTC/USDT", "buy", D("1"), None))


def test_normalization_enforces_symbol_rules(monkeypatch):
    adapter = object.__new__(BinanceSpotTestnetAdapter)
    class FakeRules:
        amount_step = D("0.001")
        price_step = D("0.01")
        min_amount = D("0.01")
        min_cost = D("10")
    monkeypatch.setattr(adapter, "symbol_rules", lambda symbol: FakeRules())

    normalized = adapter.normalize_limit(
        OrderIntent("BTC/USDT", "buy", D("0.123456"), D("100.129"), "cid-1")
    )
    assert normalized.amount == D("0.123")
    assert normalized.price == D("100.12")
    assert normalized.client_order_id == "cid-1"


def test_normalization_rejects_min_amount(monkeypatch):
    adapter = object.__new__(BinanceSpotTestnetAdapter)
    class FakeRules:
        amount_step = D("0.001")
        price_step = D("0.01")
        min_amount = D("0.01")
        min_cost = D("10")
    monkeypatch.setattr(adapter, "symbol_rules", lambda symbol: FakeRules())
    with pytest.raises(ExecutionSafetyError, match="min amount"):
        adapter.normalize_limit(OrderIntent("BTC/USDT", "buy", D("0.009"), D("100")))


def test_normalization_rejects_min_notional(monkeypatch):
    adapter = object.__new__(BinanceSpotTestnetAdapter)
    class FakeRules:
        amount_step = D("0.001")
        price_step = D("0.01")
        min_amount = D("0.001")
        min_cost = D("10")
    monkeypatch.setattr(adapter, "symbol_rules", lambda symbol: FakeRules())
    with pytest.raises(ExecutionSafetyError, match="min cost"):
        adapter.normalize_limit(OrderIntent("BTC/USDT", "buy", D("0.05"), D("100")))
