import asyncio
import time
from decimal import Decimal as D
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from dca_bot import models, worker
from dca_bot.db import Base
from dca_bot.models import PaperDealRecord, StrategyRecord
from dca_bot.paper_engine import (
    deserialize_paper_deal,
    serialize_paper_deal,
    start_paper_deal,
    update_paper_deal,
)
from dca_bot.strategy import StrategyConfig


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    return engine, session


def _config():
    return StrategyConfig(
        name="recovery-test",
        symbols=["BTC/USDT"],
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=2,
        initial_deviation_pct=1,
        deviation_scale=1,
        safety_order_volume_scale=1,
        take_profit_pct=1,
        max_capital_per_deal=100,
        max_total_allocation=300,
        fee_rate_pct_per_side=0.1,
        slippage_pct_per_side=0,
        daily_loss_limit_quote=25,
        max_equity_drawdown_pct=5,
    )


def _persist_active_deal(session):
    config = _config()
    strategy = StrategyRecord(name=config.name, config_json=config.model_dump_json(), enabled=False)
    session.add(strategy)
    session.flush()

    deal = start_paper_deal(config, "BTC/USDT", D("100"))
    update_paper_deal(deal, D("99"))
    row = PaperDealRecord(
        id=deal.id,
        strategy_id=strategy.id,
        symbol=deal.symbol,
        status=deal.status,
        state_json=serialize_paper_deal(deal),
    )
    session.add(row)
    session.commit()
    return strategy, row, deal


def test_worker_resumes_persisted_deal_without_replaying_base_or_prior_safety_fill(monkeypatch):
    engine, session = _session()
    try:
        _, row, before_restart = _persist_active_deal(session)
        persisted_before_restart = row.state_json
        assert before_restart.safety_orders_filled == 1

        class FakeMarketData:
            def ticker(self, symbol):
                return SimpleNamespace(last=D("98"))

        monkeypatch.setattr(worker, "BinanceSpotMarketData", FakeMarketData)
        result = worker.tick_active_paper_deals(session)

        assert result["failures"] == []
        assert len(result["updated"]) == 1
        row_after_restart = session.get(PaperDealRecord, row.id)
        resumed = deserialize_paper_deal(row_after_restart.state_json)

        assert resumed.id == before_restart.id
        assert resumed.status == "active"
        assert resumed.safety_orders_filled == 2
        assert resumed.quote_spent == D("30")
        assert sum(event.event_type == "base_order_filled" for event in resumed.events) == 1
        assert sum(event.event_type == "safety_order_filled" for event in resumed.events) == 2
        assert row_after_restart.state_json != persisted_before_restart
    finally:
        session.close()
        engine.dispose()


def test_worker_keeps_persisted_state_unchanged_when_market_price_fetch_fails(monkeypatch):
    engine, session = _session()
    try:
        _, row, _ = _persist_active_deal(session)
        original_state = row.state_json

        class FailingMarketData:
            def ticker(self, symbol):
                raise TimeoutError("simulated market data timeout")

        monkeypatch.setattr(worker, "BinanceSpotMarketData", FailingMarketData)
        result = worker.tick_active_paper_deals(session)

        assert result["updated"] == []
        assert len(result["failures"]) == 1
        assert result["failures"][0]["deal_id"] == row.id
        persisted = session.get(PaperDealRecord, row.id)
        assert persisted.status == "active"
        assert persisted.state_json == original_state
    finally:
        session.close()
        engine.dispose()


def test_autonomous_scanner_does_not_open_deal_when_candle_feed_fails(monkeypatch):
    engine, session = _session()
    try:
        config = _config().model_copy(update={
            "start_condition": "autonomous",
            "entry_filter": "none",
        })
        strategy = StrategyRecord(
            name=config.name,
            config_json=config.model_dump_json(),
            enabled=True,
        )
        session.add(strategy)
        session.commit()

        class FailingMarketData:
            def candles(self, symbol, timeframe, limit):
                raise TimeoutError("simulated candle feed timeout")

        monkeypatch.setattr(worker, "BinanceSpotMarketData", FailingMarketData)
        result = worker.scan_autonomous_strategies(session)

        assert result["started"] == []
        assert len(result["failures"]) == 1
        assert result["failures"][0]["strategy_id"] == strategy.id
        assert session.query(PaperDealRecord).count() == 0
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("tick_mode", ["raises", "reports_failure"])
def test_worker_skips_autonomous_scan_when_maintenance_is_unsafe(monkeypatch, tick_mode):
    stop_event = asyncio.Event()
    scan_calls = []

    async def fake_to_thread(func, *args, **kwargs):
        if func is worker.tick_active_paper_deals:
            stop_event.set()
            if tick_mode == "raises":
                raise RuntimeError("simulated database failure")
            return {"updated": [], "failures": [{"symbol": "BTC/USDT", "error": "price timeout"}]}
        if func is worker.scan_autonomous_strategies:
            scan_calls.append(True)
            return {"started": [], "waited": [], "failures": []}
        raise AssertionError(f"Unexpected worker callback: {func}")

    monkeypatch.setattr(worker.asyncio, "to_thread", fake_to_thread)
    asyncio.run(worker.run_paper_worker(stop_event, 3))

    assert scan_calls == []


def test_worker_rolls_back_deal_state_and_audit_when_commit_fails(monkeypatch):
    engine, session = _session()
    verification = None
    try:
        _, row, before = _persist_active_deal(session)
        original_state = row.state_json

        class FakeMarketData:
            def ticker(self, symbol):
                return SimpleNamespace(last=D("98"))

        monkeypatch.setattr(worker, "BinanceSpotMarketData", FakeMarketData)

        def fail_commit():
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)

        with pytest.raises(RuntimeError, match="simulated commit failure"):
            worker.tick_active_paper_deals(session)

        verification = sessionmaker(bind=engine, expire_on_commit=False)()
        persisted = verification.get(PaperDealRecord, row.id)
        assert persisted is not None
        assert persisted.status == "active"
        assert persisted.state_json == original_state
        assert verification.query(models.AuditEvent).filter_by(
            event_type="paper_deal_updated"
        ).count() == 0

        resumed = deserialize_paper_deal(persisted.state_json)
        assert resumed.safety_orders_filled == before.safety_orders_filled
        assert resumed.quote_spent == before.quote_spent
    finally:
        if verification is not None:
            verification.close()
        session.close()
        engine.dispose()



def test_worker_rolls_back_one_deal_when_audit_write_fails(monkeypatch):
    engine, session = _session()
    try:
        _, row, _ = _persist_active_deal(session)
        original_state = row.state_json

        class FakeMarketData:
            def ticker(self, symbol):
                return SimpleNamespace(last=D("98"))

        monkeypatch.setattr(worker, "BinanceSpotMarketData", FakeMarketData)
        original_add = session.add

        def fail_audit_add(instance):
            if isinstance(instance, models.AuditEvent):
                raise RuntimeError("simulated audit write failure")
            return original_add(instance)

        monkeypatch.setattr(session, "add", fail_audit_add)
        result = worker.tick_active_paper_deals(session)

        assert result["updated"] == []
        assert len(result["failures"]) == 1
        assert result["failures"][0]["deal_id"] == row.id

        verification = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            persisted = verification.get(PaperDealRecord, row.id)
            assert persisted is not None
            assert persisted.status == "active"
            assert persisted.state_json == original_state
            assert verification.query(models.AuditEvent).filter_by(
                event_type="paper_deal_updated"
            ).count() == 0
        finally:
            verification.close()
    finally:
        session.close()
        engine.dispose()



def test_autonomous_automatic_mode_scans_discovered_usdt_pairs_not_manual_symbols(monkeypatch):
    engine, session = _session()
    try:
        config = _config().model_copy(update={
            "start_condition": "autonomous",
            "entry_filter": "none",
            "pair_selection_mode": "automatic_usdt",
            "discovery_min_quote_volume_24h": 5_000_000,
            "discovery_max_spread_pct": 0.25,
            "discovery_max_candidates": 10,
        })
        strategy = StrategyRecord(
            name=config.name,
            config_json=config.model_dump_json(),
            enabled=True,
        )
        session.add(strategy)
        session.commit()

        current_hour_ms = (int(time.time() * 1000) // 3_600_000) * 3_600_000
        candles = [
            [current_hour_ms - (300 - index) * 3_600_000, 100, 101, 99, 100, 1000]
            for index in range(301)
        ]
        class FakeMarketData:
            discovered_args = None
            requested_symbols = []

            def discover_usdt_symbols(self, **kwargs):
                self.discovered_args = kwargs
                return ["ETH/USDT"]

            def candles(self, symbol, timeframe, limit):
                self.requested_symbols.append(symbol)
                return candles

        fake_market = FakeMarketData()
        monkeypatch.setattr(worker, "BinanceSpotMarketData", lambda: fake_market)
        monkeypatch.setattr(worker, "analyze_market", lambda symbol, timeframe, rows, cfg: SimpleNamespace(
            action="wait",
            reason="test decision",
            score=10,
            indicators=SimpleNamespace(
                close=100, ema_fast=100, ema_slow=101, rsi=45, atr_pct=1.2, adx=15
            ),
            parameters={},
        ))

        result = worker.scan_autonomous_strategies(session)

        assert result["started"] == []
        assert fake_market.discovered_args == {
            "min_quote_volume_24h": 5_000_000,
            "max_spread_pct": 0.25,
            "limit": 10,
        }
        assert fake_market.requested_symbols == ["ETH/USDT"]
        assert session.query(PaperDealRecord).count() == 0
        decision = session.query(models.AuditEvent).filter_by(
            event_type="autonomous_decision"
        ).one()
        assert '"symbol": "ETH/USDT"' in decision.detail
    finally:
        session.close()
        engine.dispose()



def test_autonomous_scan_rolls_back_partial_deal_when_entry_audit_fails(monkeypatch):
    engine, session = _session()
    try:
        config = _config().model_copy(update={
            "start_condition": "autonomous",
            "entry_filter": "none",
            "pair_selection_mode": "automatic_usdt",
            "max_active_deals": 2,
        })
        strategy = StrategyRecord(
            name=config.name,
            config_json=config.model_dump_json(),
            enabled=True,
        )
        session.add(strategy)
        session.commit()

        current_hour_ms = (int(time.time() * 1000) // 3_600_000) * 3_600_000
        candles = [
            [current_hour_ms - (300 - index) * 3_600_000, 100, 101, 99, 100, 1000]
            for index in range(301)
        ]

        class FakeMarketData:
            def discover_usdt_symbols(self, **kwargs):
                return ["ETH/USDT"]

            def candles(self, symbol, timeframe, limit):
                return candles

        monkeypatch.setattr(worker, "BinanceSpotMarketData", FakeMarketData)
        monkeypatch.setattr(worker, "analyze_market", lambda symbol, timeframe, rows, cfg: SimpleNamespace(
            action="candidate",
            reason="test candidate",
            score=90,
            indicators=SimpleNamespace(
                close=100, ema_fast=100, ema_slow=99, rsi=35, atr_pct=1.2, adx=25
            ),
            parameters={
                "estimated_deal_capital_quote": 50,
                "capital_safe": True,
                "initial_deviation_pct": 1.0,
                "deviation_scale": 1.2,
                "take_profit_pct": 1.0,
                "max_safety_orders": 2,
                "safety_order_volume_scale": 1.2,
            },
        ))

        original_add = session.add

        def fail_entry_audit(instance):
            if isinstance(instance, models.AuditEvent) and instance.event_type == "autonomous_entry_started":
                raise RuntimeError("simulated entry audit failure")
            return original_add(instance)

        monkeypatch.setattr(session, "add", fail_entry_audit)
        result = worker.scan_autonomous_strategies(session)

        assert result["started"] == []
        assert len(result["failures"]) == 1
        assert session.query(PaperDealRecord).filter_by(status="active").count() == 0
        assert session.query(models.AuditEvent).filter_by(
            event_type="autonomous_entry_started"
        ).count() == 0
    finally:
        session.close()
        engine.dispose()



def test_automatic_scanner_starts_highest_scoring_signal_first(monkeypatch):
    engine, session = _session()
    try:
        config = _config().model_copy(update={
            "start_condition": "autonomous",
            "entry_filter": "none",
            "pair_selection_mode": "automatic_usdt",
            "max_active_deals": 1,
        })
        strategy = StrategyRecord(
            name=config.name,
            config_json=config.model_dump_json(),
            enabled=True,
        )
        session.add(strategy)
        session.commit()

        current_hour_ms = (int(time.time() * 1000) // 3_600_000) * 3_600_000
        candles = [
            [current_hour_ms - (300 - index) * 3_600_000, 100, 101, 99, 100, 1000]
            for index in range(301)
        ]

        class FakeMarketData:
            requested_symbols = []

            def discover_usdt_symbols(self, **kwargs):
                # Deliberately return lower-scoring ETH first to prove ranking is signal-led.
                return ["ETH/USDT", "BTC/USDT"]

            def candles(self, symbol, timeframe, limit):
                self.requested_symbols.append(symbol)
                return candles

        fake_market = FakeMarketData()
        monkeypatch.setattr(worker, "BinanceSpotMarketData", lambda: fake_market)

        def fake_analyze(symbol, timeframe, rows, cfg):
            score = 80 if symbol == "ETH/USDT" else 95
            return SimpleNamespace(
                action="candidate",
                reason="test candidate",
                score=score,
                indicators=SimpleNamespace(
                    close=100, ema_fast=100, ema_slow=99, rsi=35, atr_pct=1.2, adx=25
                ),
                parameters={
                    "estimated_deal_capital_quote": 50,
                    "capital_safe": True,
                    "initial_deviation_pct": 1.0,
                    "deviation_scale": 1.2,
                    "take_profit_pct": 1.0,
                    "max_safety_orders": 2,
                    "safety_order_volume_scale": 1.2,
                },
            )

        monkeypatch.setattr(worker, "analyze_market", fake_analyze)
        result = worker.scan_autonomous_strategies(session)

        assert [item["symbol"] for item in result["started"]] == ["BTC/USDT"]
        assert sorted(fake_market.requested_symbols) == ["BTC/USDT", "ETH/USDT"]
        assert session.query(PaperDealRecord).filter_by(status="active").count() == 1
    finally:
        session.close()
        engine.dispose()



def test_invalid_enabled_profile_does_not_abort_other_autonomous_profiles(monkeypatch):
    engine, session = _session()
    try:
        invalid = StrategyRecord(
            name="broken-profile",
            config_json="{not-valid-json",
            enabled=True,
        )
        config = _config().model_copy(update={
            "start_condition": "autonomous",
            "entry_filter": "none",
            "pair_selection_mode": "automatic_usdt",
        })
        valid = StrategyRecord(
            name=config.name,
            config_json=config.model_dump_json(),
            enabled=True,
        )
        session.add_all([invalid, valid])
        session.commit()

        class EmptyDiscovery:
            def discover_usdt_symbols(self, **kwargs):
                return []

        monkeypatch.setattr(worker, "BinanceSpotMarketData", EmptyDiscovery)

        result = worker.scan_autonomous_strategies(session)

        assert result["started"] == []
        assert any(item["strategy_id"] == invalid.id for item in result["failures"])
        assert any(
            item["strategy_id"] == valid.id and item["symbol"] == "*"
            for item in result["waited"]
        )
    finally:
        session.close()
        engine.dispose()


def test_global_capital_limit_blocks_autonomous_entry_across_strategy_profiles(monkeypatch):
    engine, session = _session()
    try:
        # An existing deal from a different profile reserves its full deal cap.
        _, active_row, _ = _persist_active_deal(session)
        config = _config().model_copy(update={
            "name": "second-profile",
            "start_condition": "autonomous",
            "entry_filter": "none",
            "pair_selection_mode": "automatic_usdt",
            "max_active_deals": 2,
        })
        strategy = StrategyRecord(name=config.name, config_json=config.model_dump_json(), enabled=True)
        session.add(strategy)
        session.commit()
        monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(global_capital_limit_quote=150))

        current_hour_ms = (int(time.time() * 1000) // 3_600_000) * 3_600_000
        candles = [
            [current_hour_ms - (300 - index) * 3_600_000, 100, 101, 99, 100, 1000]
            for index in range(301)
        ]
        class FakeMarketData:
            def discover_usdt_symbols(self, **kwargs):
                return ["ETH/USDT"]
            def candles(self, symbol, timeframe, limit):
                return candles

        monkeypatch.setattr(worker, "BinanceSpotMarketData", FakeMarketData)
        monkeypatch.setattr(worker, "analyze_market", lambda symbol, timeframe, rows, cfg: SimpleNamespace(
            action="candidate", reason="test candidate", score=90,
            indicators=SimpleNamespace(close=100, ema_fast=100, ema_slow=99, rsi=35, atr_pct=1.2, adx=25),
            parameters={"estimated_deal_capital_quote": 50, "capital_safe": True,
                "initial_deviation_pct": 1.0, "deviation_scale": 1.2, "take_profit_pct": 1.0,
                "max_safety_orders": 2, "safety_order_volume_scale": 1.2},
        ))

        result = worker.scan_autonomous_strategies(session)
        assert result["started"] == []
        assert any("Global capital limit" in item.get("reason", "") for item in result["waited"])
        assert session.query(PaperDealRecord).filter_by(status="active").count() == 1
        assert session.get(PaperDealRecord, active_row.id) is not None
    finally:
        session.close()
        engine.dispose()



def test_global_capital_status_fails_closed_on_persisted_status_mismatch():
    from dca_bot.risk import global_capital_status

    engine, session = _session()
    try:
        _, row, deal = _persist_active_deal(session)
        deal.status = "closed"
        row.state_json = serialize_paper_deal(deal)
        session.commit()

        with pytest.raises(ValueError, match="Persisted active deal state is inconsistent"):
            global_capital_status(session, D("120"))
    finally:
        session.close()
        engine.dispose()
