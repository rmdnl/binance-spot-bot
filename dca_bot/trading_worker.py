from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .execution_factory import build_execution_backend
from .worker import run_paper_worker

logger = logging.getLogger(__name__)


async def run_trading_worker(stop_event: asyncio.Event, settings: Settings) -> None:
    """Route the process to the explicitly selected safe execution backend.

    Paper remains the existing regression path. Testnet is deliberately
    isolated behind its own worker entry point until the exchange lifecycle
    integration is complete. Live mode is rejected by Settings.assert_safe().
    """
    settings.assert_safe()
    mode = settings.trading_mode.lower().strip()

    if mode == "paper":
        await run_paper_worker(stop_event, settings.paper_poll_interval_seconds)
        return

    if mode == "testnet":
        backend = build_execution_backend(settings)
        if backend is None:
            raise RuntimeError("Testnet mode requires a Binance execution backend")
        # Deliberately fail closed until the full exchange deal lifecycle is
        # wired into the autonomous worker. Initializing the adapter here proves
        # credentials, sandbox mode, and market metadata can be loaded without
        # placing an order.
        logger.info("Binance Spot Testnet backend initialized; order lifecycle wiring pending")
        while not stop_event.is_set():
            await asyncio.sleep(settings.paper_poll_interval_seconds)
        return

    raise RuntimeError(f"Unsupported trading mode: {mode}")
