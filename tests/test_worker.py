import asyncio
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
