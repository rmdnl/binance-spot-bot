from decimal import Decimal as D

import pytest

from dca_bot.execution import OrderResult
from dca_bot.order_state import (
    LocalOrderStatus,
    OrderSnapshot,
    apply_exchange_result,
    reconcile_by_client_id,
    should_submit,
)


def local(status=LocalOrderStatus.INTENT, exchange_order_id=None):
    return OrderSnapshot(
        client_order_id="dca-test-001",
        symbol="BTC/USDT",
        side="buy",
        requested_amount=D("0.01"),
        requested_price=D("100000"),
        exchange_order_id=exchange_order_id,
        status=status,
    )


def result(status, client_id="dca-test-001", order_id="123", filled="0", average=None):
    return OrderResult(
        order_id=order_id,
        client_order_id=client_id,
        symbol="BTC/USDT",
        side="buy",
        status=status,
        amount=D("0.01"),
        filled=D(filled),
        remaining=D("0.01") - D(filled),
        average=D(average) if average else None,
        price=D("100000"),
        raw={},
    )


def test_open_order_is_adopted_without_duplicate_submission():
    decision = reconcile_by_client_id(local(), [result("open")])
    assert decision.action == "adopt"
    assert decision.snapshot.status == LocalOrderStatus.OPEN
    assert decision.snapshot.exchange_order_id == "123"
    assert should_submit(decision.snapshot) is False


def test_partial_fill_becomes_partial_filled():
    decision = reconcile_by_client_id(
        local(), [result("open", filled="0.004", average="99900")]
    )
    assert decision.snapshot.status == LocalOrderStatus.PARTIALLY_FILLED
    assert decision.snapshot.filled_amount == D("0.004")
    assert decision.snapshot.average_price == D("99900")


def test_closed_exchange_order_becomes_filled():
    decision = reconcile_by_client_id(
        local(), [result("closed", filled="0.01", average="100010")]
    )
    assert decision.snapshot.status == LocalOrderStatus.FILLED
    assert decision.snapshot.filled_amount == D("0.01")


def test_missing_client_id_match_requires_lookup_not_resubmission():
    decision = reconcile_by_client_id(local(), [result("open", client_id="other")])
    assert decision.action == "query_required"
    assert should_submit(decision.snapshot) is True


def test_duplicate_client_id_halts():
    decision = reconcile_by_client_id(local(), [result("open", order_id="1"), result("open", order_id="2")])
    assert decision.action == "halt"


def test_exchange_result_rejects_symbol_mismatch():
    bad = result("open")
    bad = OrderResult(**{**bad.__dict__, "symbol": "ETH/USDT"})
    with pytest.raises(ValueError, match="symbol"):
        apply_exchange_result(local(), bad)


def test_exchange_result_rejects_overfill():
    with pytest.raises(ValueError, match="filled amount"):
        apply_exchange_result(local(), result("closed", filled="0.02"))
