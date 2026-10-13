from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

from dca_bot.entry_guard import serialized_entry


def test_serialized_entry_prevents_overlapping_entry_checks():
    entered = Event()
    release = Event()
    calls = []

    @serialized_entry
    def entry(label):
        calls.append((label, "start"))
        if label == "first":
            entered.set()
            assert release.wait(timeout=2)
        calls.append((label, "end"))
        return label

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(entry, "first")
        assert entered.wait(timeout=1)
        second = pool.submit(entry, "second")
        time.sleep(0.05)
        assert calls == [("first", "start")]
        release.set()
        assert first.result(timeout=2) == "first"
        assert second.result(timeout=2) == "second"

    assert calls == [
        ("first", "start"),
        ("first", "end"),
        ("second", "start"),
        ("second", "end"),
    ]



def test_paper_worker_state_paths_use_shared_serialization_guard():
    from dca_bot.worker import scan_autonomous_strategies, tick_active_paper_deals

    assert getattr(scan_autonomous_strategies, "__wrapped__", None) is not None
    assert getattr(tick_active_paper_deals, "__wrapped__", None) is not None
