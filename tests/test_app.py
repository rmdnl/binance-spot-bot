import pytest
from fastapi.testclient import TestClient
from dca_bot import main
from dca_bot.main import app
from dca_bot.strategy import StrategyConfig


def test_health_reports_execution_disabled():
    response = TestClient(app).get('/healthz')
    assert response.status_code == 200
    assert response.json()['execution'] == 'disabled'


def test_dashboard_redirects_to_login_without_session():
    response = TestClient(app).get('/', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login'


def test_login_page_does_not_expose_dashboard():
    response = TestClient(app).get('/login')
    assert response.status_code == 200
    assert 'Dashboard password' in response.text


def test_strategy_analysis_uses_only_fresh_closed_candles(monkeypatch):
    import time
    from types import SimpleNamespace

    from dca_bot import main

    monkeypatch.setattr(main, "ensure_authenticated", lambda request: None)
    now_ms = int(time.time() * 1000)
    candles = [
        [now_ms - (300 - index) * 60_000, 100, 101, 99, 100, 10]
        for index in range(300)
    ]
    seen = {}

    class FakeMarketData:
        normalize_symbol = staticmethod(main.BinanceSpotMarketData.normalize_symbol)

        def candles(self, symbol, timeframe, limit):
            return candles

    def fake_analyze(symbol, timeframe, observed_candles, config):
        seen["candles"] = observed_candles
        return SimpleNamespace(to_dict=lambda: {"action": "wait", "execution": "disabled"})

    monkeypatch.setattr(main, "BinanceSpotMarketData", FakeMarketData)
    monkeypatch.setattr(main, "analyze_market", fake_analyze)

    response = TestClient(app).get("/api/strategy/analyze?symbol=BTCUSDT&timeframe=1h")

    assert response.status_code == 200
    assert len(seen["candles"]) == len(candles) - 1
    assert seen["candles"][-1] == candles[-2]
    assert response.json()["execution"] == "disabled"


def test_strategy_analysis_rejects_stale_candles(monkeypatch):
    import time

    from dca_bot import main

    monkeypatch.setattr(main, "ensure_authenticated", lambda request: None)
    old_start_ms = int((time.time() - 8 * 24 * 60 * 60) * 1000)
    candles = [
        [old_start_ms + index * 60_000, 100, 101, 99, 100, 10]
        for index in range(300)
    ]

    class FakeMarketData:
        normalize_symbol = staticmethod(main.BinanceSpotMarketData.normalize_symbol)

        def candles(self, symbol, timeframe, limit):
            return candles

    monkeypatch.setattr(main, "BinanceSpotMarketData", FakeMarketData)
    response = TestClient(app).get("/api/strategy/analyze?symbol=BTCUSDT&timeframe=1h")

    assert response.status_code == 422
    assert "stale" in response.json()["detail"]


def test_automatic_fixed_coin_profile_defers_capital_estimate_to_each_candidate():
    config = StrategyConfig(
        pair_selection_mode="automatic_usdt",
        averaging_method="fixed_coin_amount",
        max_capital_per_deal=200,
        max_total_allocation=600,
        max_active_deals=3,
    )

    required, total_capacity = main.estimate_profile_capital(config, None)

    assert required is None
    assert total_capacity == 600


def test_manual_fixed_coin_profile_still_requires_reference_price():
    config = StrategyConfig(
        pair_selection_mode="manual",
        averaging_method="fixed_coin_amount",
        max_capital_per_deal=200,
        max_total_allocation=600,
        max_active_deals=3,
    )

    with pytest.raises(ValueError, match="positive reference price"):
        main.estimate_profile_capital(config, None)



def test_automatic_fixed_quote_profile_can_use_adaptive_ladder_within_cap():
    config = StrategyConfig(
        pair_selection_mode="automatic_usdt",
        averaging_method="fixed_order_value",
        base_order_quote=10,
        safety_order_quote=100,
        max_safety_orders=4,
        safety_order_volume_scale=2,
        max_capital_per_deal=200,
        max_total_allocation=600,
        max_active_deals=3,
    )

    required, total_capacity = main.estimate_profile_capital(config, None)

    # The configured maximum ladder exceeds 200 USDT, but the autonomous
    # analyzer can reduce safety-order count per candidate before opening.
    assert required is None
    assert total_capacity == 600


def test_paper_entry_persistence_failure_rolls_back_and_fails_closed():
    class FailingSession:
        def __init__(self):
            self.added = []
            self.rolled_back = False

        def add(self, item):
            self.added.append(item)

        def commit(self):
            raise RuntimeError("simulated database commit failure")

        def rollback(self):
            self.rolled_back = True

    session = FailingSession()
    # Use a real paper-engine deal while the database commit is unavailable.
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=0,
        max_capital_per_deal=20,
        max_total_allocation=20,
    )
    from dca_bot.paper_engine import start_paper_deal
    real_deal = start_paper_deal(config, "BTC/USDT", 100)

    with pytest.raises(main.HTTPException) as exc:
        main.persist_paper_entry(session, 7, real_deal)

    assert exc.value.status_code == 503
    assert "inspect persisted deal state before retrying" in exc.value.detail
    assert len(session.added) == 2
    assert session.rolled_back is True
