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
