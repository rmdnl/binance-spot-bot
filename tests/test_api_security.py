from fastapi.testclient import TestClient

from dca_bot.main import app


def test_market_ticker_api_requires_authentication():
    response = TestClient(app).get("/api/market/ticker?symbol=BTCUSDT")
    assert response.status_code == 401


def test_dca_preview_api_requires_authentication():
    response = TestClient(app).get("/api/dca/preview?base_price=100")
    assert response.status_code == 401



def test_paper_deals_api_requires_authentication():
    response = TestClient(app).get("/api/paper/deals")
    assert response.status_code == 401


def test_paper_deal_start_api_requires_authentication():
    response = TestClient(app).post(
        "/api/paper/deals/start",
        data={"csrf_token": "invalid", "strategy_id": "1", "symbol": "BTC/USDT"},
    )
    assert response.status_code == 401
