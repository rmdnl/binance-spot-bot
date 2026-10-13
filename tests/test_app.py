from fastapi.testclient import TestClient
from dca_bot.main import app


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
