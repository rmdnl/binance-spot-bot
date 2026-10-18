from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from spot_bot import grid_api
from spot_bot.main import app


GRID_ENDPOINTS = [
    ("/api/grid/bots", {
        "environment": "testnet",
        "symbol": "BTC/USDT",
        "lower_price": "80000",
        "upper_price": "90000",
        "grids": "5",
        "capital_quote": "500",
        "grid_type": "arithmetic",
        "max_open_orders": "3",
        "csrf_token": "invalid",
    }),
    ("/api/grid/bots/1/start", {"csrf_token": "invalid"}),
    ("/api/grid/bots/1/pause", {"csrf_token": "invalid"}),
    ("/api/grid/bots/1/resume", {"csrf_token": "invalid"}),
    ("/api/grid/bots/1/stop", {"csrf_token": "invalid"}),
]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(grid_api, "auth", lambda _request: False)
    monkeypatch.setattr(grid_api, "csrf_valid", lambda _token, _request: True)
    monkeypatch.setattr(
        grid_api,
        "adapter_for",
        lambda _bot: pytest.fail("Unauthenticated request reached the exchange adapter"),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(("path", "data"), GRID_ENDPOINTS)
def test_grid_mutations_require_authentication(
    client: TestClient, path: str, data: dict[str, str]
) -> None:
    response = client.post(path, data=data)

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


@pytest.mark.parametrize(("path", "data"), GRID_ENDPOINTS)
def test_grid_mutations_reject_invalid_csrf_with_forbidden(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    data: dict[str, str],
) -> None:
    monkeypatch.setattr(grid_api, "auth", lambda _request: True)
    monkeypatch.setattr(grid_api, "csrf_valid", lambda _token, _request: False)

    response = client.post(path, data=data)

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid CSRF token"}
