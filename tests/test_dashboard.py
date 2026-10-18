from fastapi.testclient import TestClient
from spot_bot.main import app


def test_healthz_is_public_and_exposes_minimal_status():
    response = TestClient(app).get('/healthz')
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_is_disabled():
    response = TestClient(app).get('/openapi.json')
    assert response.status_code == 404


def test_dashboard_requires_authentication():
    response = TestClient(app).get('/', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login'


def test_account_keeps_balance_and_equity_when_recent_trades_fail(monkeypatch):
    from spot_bot import main
    from spot_bot.models import Environment

    class FakeExchange:
        def __init__(self, environment):
            assert environment == Environment.TESTNET

        def balance(self):
            return {"total": {"USDT": 125.5}, "free": {"USDT": 100}, "used": {"USDT": 25.5}}

        def recent_trades(self, limit=25):
            raise RuntimeError("trade history temporarily unavailable")

    expected_metrics = {
        "equity_usdt": "125.5",
        "equity_complete": True,
        "unpriced_assets": [],
        "realized_pnl_usdt": "0",
        "realized_pnl_complete": True,
        "unpriced_realized_quotes": [],
        "unrealized_pnl_usdt": "0",
        "unrealized_pnl_complete": True,
        "unpriced_positions": [],
    }
    monkeypatch.setattr(main, "require_auth", lambda request: None)
    monkeypatch.setattr("spot_bot.exchange.BinanceSpot", FakeExchange)
    monkeypatch.setattr(main, "dashboard_metrics", lambda db, ex, environment, **kwargs: expected_metrics)

    response = TestClient(main.app).get("/api/account")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["balances"] == [
        {"asset": "USDT", "free": "100", "used": "25.5", "total": "125.5"}
    ]
    assert payload["metrics"] == expected_metrics
    assert payload["trades"] == []
    assert "trade history temporarily unavailable" in payload["trades_error"]


def test_account_keeps_balance_when_metrics_calculation_fails(monkeypatch):
    from spot_bot import main
    from spot_bot.models import Environment

    class FakeExchange:
        def __init__(self, environment):
            assert environment == Environment.TESTNET

        def balance(self):
            return {"total": {"USDT": 42}, "free": {"USDT": 42}, "used": {}}

        def recent_trades(self, limit=25):
            return []

    monkeypatch.setattr(main, "require_auth", lambda request: None)
    monkeypatch.setattr("spot_bot.exchange.BinanceSpot", FakeExchange)

    def fail_metrics(db, ex, environment, **kwargs):
        raise RuntimeError("market data unavailable")

    monkeypatch.setattr(main, "dashboard_metrics", fail_metrics)
    response = TestClient(main.app).get("/api/account")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["balances"][0]["total"] == "42"
    assert payload["trades"] == []
    assert payload["metrics"] is None
    assert "market data unavailable" in payload["metrics_error"]


def test_risk_status_never_reports_placeholder_zero_metrics(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from spot_bot import main
    from spot_bot.db import get_db
    from spot_bot.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        main.app.dependency_overrides[get_db] = lambda: db
        monkeypatch.setattr(main, "require_auth", lambda request: None)
        try:
            response = TestClient(main.app).get("/api/risk/status")
        finally:
            main.app.dependency_overrides.clear()
    engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["equity_quote"] is None
    assert payload["drawdown_pct"] is None
    assert payload["daily_realized_pnl_quote"] is None
    assert payload["metrics_available"] is False


def test_risk_status_persists_peak_equity_and_calculates_drawdown(monkeypatch):
    from decimal import Decimal
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from spot_bot import main
    from spot_bot.db import get_db
    from spot_bot.models import Base, EquityHighWaterMark, Environment

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    values = iter(["100", "90", "110", "88"])

    class FakeExchange:
        def __init__(self, environment):
            assert environment == Environment.TESTNET

        def ticker(self, symbol):
            return {"last": "1"}

    monkeypatch.setattr(main, "require_auth", lambda request: None)
    monkeypatch.setattr("spot_bot.exchange.BinanceSpot", FakeExchange)
    monkeypatch.setattr(main, "dashboard_metrics", lambda *args, **kwargs: {
        "equity_usdt": next(values), "equity_complete": True
    })

    with Session(engine) as db:
        main.app.dependency_overrides[get_db] = lambda: db
        try:
            client = TestClient(main.app)
            first = client.get("/api/risk/status").json()
            second = client.get("/api/risk/status").json()
            third = client.get("/api/risk/status").json()
            fourth = client.get("/api/risk/status").json()
            mark = db.scalar(select(EquityHighWaterMark).where(
                EquityHighWaterMark.environment == Environment.TESTNET.value
            ))
        finally:
            main.app.dependency_overrides.clear()

    assert Decimal(first["drawdown_pct"]) == Decimal("0")
    assert Decimal(second["drawdown_pct"]) == Decimal("10")
    assert Decimal(third["drawdown_pct"]) == Decimal("0")
    assert Decimal(fourth["drawdown_pct"]) == Decimal("20")
    assert Decimal(str(mark.peak_equity)) == Decimal("110")
    engine.dispose()
