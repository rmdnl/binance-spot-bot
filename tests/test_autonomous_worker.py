import threading

import dca_bot.autonomous_worker as worker


def test_worker_stops_without_running_cycle(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "run_testnet_cycle_once", lambda settings: calls.append(settings))
    stop = threading.Event()
    stop.set()
    settings = type("Settings", (), {"reconciliation_interval_seconds": 1})()
    worker.run_autonomous_testnet_worker(settings, stop)
    assert calls == []
