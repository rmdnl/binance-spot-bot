from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .autonomous_testnet_runtime import run_testnet_cycle_once

logger = logging.getLogger(__name__)


def run_autonomous_testnet_worker(settings: Any, stop_event: threading.Event) -> None:
    """Run the canonical autonomous Testnet lifecycle until stopped."""
    interval = max(1, int(settings.reconciliation_interval_seconds))
    while not stop_event.is_set():
        try:
            result = run_testnet_cycle_once(settings)
            logger.info("autonomous testnet cycle: %s", result)
        except Exception:
            logger.exception("autonomous testnet cycle failed closed")
        stop_event.wait(interval)
