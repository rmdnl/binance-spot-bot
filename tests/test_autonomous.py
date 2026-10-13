from dca_bot.autonomous import analyze_market, calculate_indicators
from dca_bot.strategy import StrategyConfig
import pytest


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
        "max_capital_per_deal": 200,
        "max_total_allocation": 600,
    }
    values.update(overrides)
    return StrategyConfig(**values)


def candles_from_closes(closes):
    candles = []
    for index, close in enumerate(closes):
        close = float(close)
        candles.append([
            index * 60_000,
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


def test_autonomous_engine_blocks_clear_downtrend_and_returns_bounded_parameters():
    closes = [200 - i * 0.7 for i in range(100)]
    result = analyze_market("BTCUSDT", "15m", candles_from_closes(closes), config())

    assert result.action == "wait"
    assert "Downtrend" in result.reason
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
    assert first["started"] == []
    assert first["failures"] == []
    assert first["waited"][0]["reason"].startswith("Downtrend")
    assert session.query(PaperDealRecord).count() == 0

    # The same closed candle is not evaluated twice.
    second = worker.scan_autonomous_strategies(session)
    assert second == {"started": [], "waited": [], "failures": []}
    events = session.query(AuditEvent).filter_by(event_type="autonomous_decision").all()
    assert len(events) == 1
    detail = json.loads(events[0].detail)
    assert detail["action"] == "wait"
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
    cfg = config(start_condition="autonomous", symbols=["BTC/USDT"])
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
