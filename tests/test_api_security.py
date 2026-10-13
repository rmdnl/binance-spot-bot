from fastapi.testclient import TestClient

from dca_bot.main import app


def test_market_ticker_api_requires_authentication():
    response = TestClient(app).get("/api/market/ticker?symbol=BTCUSDT")
    assert response.status_code == 401


def test_dca_preview_api_requires_authentication():
    response = TestClient(app).get("/api/dca/preview?base_price=100")
    assert response.status_code == 401
