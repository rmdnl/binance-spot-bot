from __future__ import annotations

from .config import Settings
from .execution import BinanceSpotTestnetAdapter, ExecutionAdapter, ExecutionSafetyError


def build_execution_adapter(settings: Settings) -> ExecutionAdapter:
    """Build the explicitly selected execution backend.

    Paper mode intentionally returns no exchange adapter. Callers must keep
    paper simulation on its dedicated engine rather than accidentally routing
    simulated orders to an exchange.
    """
    mode = settings.trading_mode.lower().strip()
    if mode == "paper":
        raise ExecutionSafetyError("Paper mode has no exchange execution adapter")
    if mode == "testnet":
        return BinanceSpotTestnetAdapter(
            settings.binance_api_key,
            settings.binance_api_secret,
            testnet=settings.binance_testnet,
        )
    raise ExecutionSafetyError(f"Unsupported trading mode: {settings.trading_mode}")
