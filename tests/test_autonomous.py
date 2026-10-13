from dca_bot.autonomous import analyze_market, calculate_indicators
from dca_bot.strategy import StrategyConfig
import pytest
import time


def config(**overrides):
    values = {
        "symbols": ["BTC/USDT"],
        "ema_fast": 10,
        "ema_slow": 30,
        "rsi_period": 14,
        "atr_period": 14,
        "max_safety_orders": 4,
        "base_order_quote": 10,
        "safety_order_quote": 10,
        "max_capital_per_deal": 100,
        "max_total_allocation": 120,
        "max_active_deals": 1,
    }
    values.update(overrides)
    return StrategyConfig(**values)


def candles_from_closes(closes):
    candles = []
    start_timestamp = int(time.time() * 1000) - len(closes) * 60_000
    for index, close in enumerate(closes):
        close = float(close)
        candles.append([
            start_timestamp + index * 60_000,
            close * 0.999,
            close * 1.01,
            close * 0.99,
            close,
            100.0,
        ])
    return candles


def test_autonomous_analysis_fails_closed_when_candle_history_is_too_short():
    with pytest.raises(ValueError, match="OHLCV candles"):
        calculate_indicators(candles_from_closes(range(100, 120)), config())


def test_dca_first_engine_allows_a_bearish_market_when_risk_bounds_pass():
    # DCA safety orders are meant to average down. A bearish EMA cross alone
    # must not turn the bot into a trend-following strategy.
    closes = [200 - i * 0.7 for i in range(100)]
    result = analyze_market("BTCUSDT", "15m", candles_from_closes(closes), config())

    assert result.action == "candidate"
    assert "DCA-first" in result.reason
    assert result.symbol == "BTC/USDT"
    assert 0.5 <= result.parameters["initial_deviation_pct"] <= 3.0
    assert 1.2 <= result.parameters["deviation_scale"] <= 1.8
    assert result.parameters["capital_safe"] is True
    assert result.parameters["estimated_deal_capital_quote"] <= 200


def test_autonomous_engine_reduces_safety_orders_to_respect_capital_limit():
    cfg = config(
        max_safety_orders=8,
        base_order_quote=20,
        safety_order_quote=30,
        safety_order_volume_scale=2,
        max_capital_per_deal=80,
        max_total_allocation=100,
    )
    closes = [100 + i * 0.1 for i in range(100)]
    result = analyze_market("BTC/USDT", "1h", candles_from_closes(closes), cfg)

    assert result.parameters["max_safety_orders"] < 8
    assert result.parameters["capital_safe"] is True
    assert result.parameters["estimated_deal_capital_quote"] <= 80


def test_autonomous_engine_rejects_invalid_close_prices():
    candles = candles_from_closes([100 + i for i in range(100)])
    candles[-1][4] = float("nan")
    with pytest.raises(ValueError, match="non-finite OHLCV"):
        calculate_indicators(candles, config())



def test_enabled_autonomous_profile_scans_closed_candle_without_real_orders(monkeypatch):
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from dca_bot.db import Base
    from dca_bot.models import AuditEvent, PaperDealRecord, StrategyRecord
    from dca_bot import worker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    cfg = config(start_condition="autonomous", symbols=["BTC/USDT"])
    record = StrategyRecord(name=cfg.name, config_json=cfg.model_dump_json(), enabled=True)
    session.add(record)
    session.commit()

    closes = [200 - i * 0.2 for i in range(350)]
    candles = candles_from_closes(closes)

    class FakeMarketData:
        def candles(self, symbol, timeframe, limit=500):
            return candles[-limit:]

    monkeypatch.setattr(worker, "BinanceSpotMarketData", FakeMarketData)
    first = worker.scan_autonomous_strategies(session)
    assert len(first["started"]) == 1
    assert first["failures"] == []
    assert first["waited"] == []
    assert session.query(PaperDealRecord).count() == 1

    # The same closed candle is not evaluated twice.
    second = worker.scan_autonomous_strategies(session)
    # The active-deal guard runs before the candle-idempotency check, so the
    # second scan reports the existing deal without evaluating/logging again.
    assert second["started"] == []
    assert second["failures"] == []
    assert second["waited"][0]["reason"] == "active deal limit"
    events = session.query(AuditEvent).filter_by(event_type="autonomous_decision").all()
    assert len(events) == 1
    detail = json.loads(events[0].detail)
    assert detail["action"] == "candidate"
    assert detail["candle_timestamp_ms"] == candles[-2][0]
    session.close()
    engine.dispose()



def test_autonomous_candidate_opens_only_a_simulated_paper_deal(monkeypatch):
    import math
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from dca_bot.db import Base
    from dca_bot.models import PaperDealRecord, StrategyRecord
    from dca_bot import worker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    cfg = config(start_condition="autonomous", symbols=["BTC/USDT"], rsi_max_entry=65, adx_min=12)
    session.add(StrategyRecord(name=cfg.name, config_json=cfg.model_dump_json(), enabled=True))
    session.commit()

    closes = [100 + i * 0.03 + 0.5 * math.sin(i * 0.3 + 1.2) for i in range(350)]
    candles = candles_from_closes(closes)

    class FakeMarketData:
        def candles(self, symbol, timeframe, limit=500):
            return candles[-limit:]

    monkeypatch.setattr(worker, "BinanceSpotMarketData", FakeMarketData)
    result = worker.scan_autonomous_strategies(session)

    assert len(result["started"]) == 1
    assert result["started"][0]["symbol"] == "BTC/USDT"
    assert result["failures"] == []
    assert session.query(PaperDealRecord).count() == 1
    paper_deal = session.query(PaperDealRecord).one()
    assert paper_deal.status == "active"
    assert "orders_sent_to_exchange" not in result
    session.close()
    engine.dispose()



def test_autonomous_engine_rejects_inconsistent_ohlc_data():
    candles = candles_from_closes([100 + i * 0.1 for i in range(100)])
    candles[-1][2] = candles[-1][4] * 0.5  # high below close is invalid
    with pytest.raises(ValueError, match="inconsistent high/low"):
        calculate_indicators(candles, config())


def test_autonomous_scanner_rejects_stale_closed_candles(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from dca_bot.db import Base
    from dca_bot.models import PaperDealRecord, StrategyRecord
    from dca_bot import worker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        cfg = config(start_condition="autonomous", symbols=["BTC/USDT"], timeframe="1h")
        session.add(StrategyRecord(name=cfg.name, config_json=cfg.model_dump_json(), enabled=True))
        session.commit()

        stale_candles = candles_from_closes([200 - i * 0.2 for i in range(350)])
        stale_by_ms = int((time.time() - 7 * 24 * 60 * 60) * 1000)
        for index, candle in enumerate(stale_candles):
            candle[0] = stale_by_ms + index * 60_000

        class StaleMarketData:
            def candles(self, symbol, timeframe, limit=500):
                return stale_candles[-limit:]

        monkeypatch.setattr(worker, "BinanceSpotMarketData", StaleMarketData)
        result = worker.scan_autonomous_strategies(session)

        assert result["started"] == []
        assert len(result["failures"]) == 1
        assert session.query(PaperDealRecord).count() == 0
    finally:
        session.close()
        engine.dispose()



def test_configured_rsi_max_entry_blocks_overheated_market():
    cfg = config(rsi_max_entry=40, adx_min=0)
    closes = [100 + index * 0.5 for index in range(100)]

    result = analyze_market("BTC/USDT", "1h", candles_from_closes(closes), cfg)

    assert result.action == "wait"
    assert "configured maximum" in result.reason
    assert result.indicators.rsi > cfg.rsi_max_entry


def test_configured_adx_minimum_blocks_weak_market():
    cfg = config(rsi_max_entry=65, adx_min=20)
    closes = [100.0] * 100

    result = analyze_market("BTC/USDT", "1h", candles_from_closes(closes), cfg)

    assert result.action == "wait"
    assert "ADX is below the configured minimum" in result.reason
    assert result.indicators.adx < cfg.adx_min
