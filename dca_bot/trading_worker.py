from __future__ import annotations

import asyncio
import logging
import threading

from .autonomous_worker import run_autonomous_testnet_worker
from .config import Settings
from .worker import run_paper_worker

logger = logging.getLogger(__name__)


async def run_trading_worker(stop_event: asyncio.Event, settings: Settings) -> None:
    """Route execution to the explicitly selected safe worker."""
    settings.assert_safe()
    mode = settings.trading_mode.lower().strip()

    if mode == "paper":
        await run_paper_worker(stop_event, settings.paper_poll_interval_seconds)
        return

    if mode == "testnet":
        thread_stop = threading.Event()

        def bridge_stop() -> None:
            async def watch() -> None:
                await stop_event.wait()
                thread_stop.set()
            asyncio.run(watch())

        bridge = threading.Thread(target=bridge_stop, name="testnet-stop-bridge", daemon=True)
        bridge.start()
        try:
            await asyncio.to_thread(run_autonomous_testnet_worker, settings, thread_stop)
        finally:
            thread_stop.set()
        return

    raise RuntimeError(f"Unsupported trading mode: {mode}")
