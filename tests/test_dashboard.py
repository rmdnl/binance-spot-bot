from fastapi.testclient import TestClient
from spot_bot.main import app


def test_healthz_is_public_and_execution_is_fail_closed():
    response = TestClient(app).get('/healthz')
    assert response.status_code == 200
    assert response.json()['execution_enabled'] is False


def test_dashboard_requires_authentication():
    response = TestClient(app).get('/', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == '/login'
