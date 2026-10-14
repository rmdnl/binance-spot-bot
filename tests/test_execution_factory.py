import pytest

from dca_bot.config import Settings
from dca_bot.execution import ExecutionSafetyError
from dca_bot.execution_factory import build_execution_adapter


def test_paper_mode_has_no_exchange_adapter():
    settings = Settings(trading_mode="paper")
    with pytest.raises(ExecutionSafetyError):
        build_execution_adapter(settings)


def test_testnet_requires_testnet_flag_and_credentials():
    settings = Settings(
        trading_mode="testnet",
        binance_testnet=False,
        binance_api_key="key",
        binance_api_secret="secret",
    )
    with pytest.raises(ValueError):
        settings.assert_safe()


def test_live_mode_is_rejected():
    settings = Settings(trading_mode="live", live_trading_enabled=True)
    with pytest.raises(ValueError):
        settings.assert_safe()
