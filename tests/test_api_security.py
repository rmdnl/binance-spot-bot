from fastapi.testclient import TestClient

from dca_bot.main import app, csrf_serializer, session_serializer


def test_dashboard_requires_signed_login():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_static_authenticated_cookie_is_rejected():
    client = TestClient(app)
    client.cookies.set("dashboard_session", "authenticated")
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_sets_matching_preauth_and_csrf():
    client = TestClient(app)
    response = client.get("/login")
    assert response.status_code == 200
    preauth = client.cookies.get("dashboard_preauth")
    csrf = response.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    payload = csrf_serializer.loads(csrf)
    assert payload["nonce"] == preauth


def test_logout_requires_signed_session_and_csrf():
    client = TestClient(app)
    response = client.post("/logout", data={"csrf_token": "invalid"})
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_accepts_valid_signed_session():
    client = TestClient(app)
    token = session_serializer.dumps({"authenticated": True, "nonce": "test"})
    client.cookies.set("dashboard_session", token)
    response = client.get("/")
    assert response.status_code == 200
    assert "TRADING DISABLED" in response.text
