from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

from .config import Settings
from .db import init_db

BASE_DIR = Path(__file__).resolve().parent
settings = Settings()
# App import and public health checks must remain possible before an operator configures secrets.
# Any privileged dashboard view/login is denied until proper secrets are configured.
app = FastAPI(title="Binance Spot Bot", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
session_serializer = URLSafeTimedSerializer(settings.dashboard_session_secret or "unconfigured-session-secret-deny-all", salt="spot-dashboard-session-v1")
csrf_serializer = URLSafeTimedSerializer(settings.dashboard_session_secret or "unconfigured-session-secret-deny-all", salt="spot-dashboard-csrf-v1")

_attempts: dict[str, deque[float]] = defaultdict(deque)
_attempts_lock = Lock()


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def login_limited(request: Request) -> bool:
    now, key = time.monotonic(), client_key(request)
    with _attempts_lock:
        attempts = _attempts[key]
        while attempts and now - attempts[0] > settings.dashboard_login_window_seconds:
            attempts.popleft()
        return len(attempts) >= settings.dashboard_login_max_attempts


def record_failed_login(request: Request) -> None:
    with _attempts_lock:
        _attempts[client_key(request)].append(time.monotonic())


def clear_failed_logins(request: Request) -> None:
    with _attempts_lock:
        _attempts.pop(client_key(request), None)


def security_configured() -> bool:
    return bool(settings.dashboard_password_hash.strip() and len(settings.dashboard_session_secret) >= 32)


def csrf_valid(token: str, request: Request) -> bool:
    try:
        payload = csrf_serializer.loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return False
    session = request.cookies.get("dashboard_session", "")
    if session:
        expected = hashlib.sha256(session.encode()).hexdigest()
        return hmac.compare_digest(payload.get("session", ""), expected)
    preauth = request.cookies.get("dashboard_preauth", "")
    return bool(preauth) and hmac.compare_digest(payload.get("nonce", ""), preauth)


def require_auth(request: Request) -> bool:
    if not security_configured():
        return False
    token = request.cookies.get("dashboard_session", "")
    if not token:
        return False
    try:
        payload = session_serializer.loads(token, max_age=settings.dashboard_session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return False
    return payload.get("authenticated") is True


def csrf_for_request(request: Request) -> str:
    session = request.cookies.get("dashboard_session", "")
    if session:
        payload = {"session": hashlib.sha256(session.encode()).hexdigest()}
    else:
        payload = {"nonce": request.cookies.get("dashboard_preauth", "")}
    return csrf_serializer.dumps(payload)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "binance-spot-bot", "trading": "disabled_until_execution_validation"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    preauth = request.cookies.get("dashboard_preauth") or secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request, "login.html",
        {"error": None, "csrf_token": csrf_serializer.dumps({"nonce": preauth})},
    )
    response.set_cookie(
        "dashboard_preauth", preauth, httponly=True,
        secure=settings.dashboard_cookie_secure, samesite="strict", max_age=900, path="/",
    )
    return response


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...), csrf_token: str = Form(...)):
    if not security_configured():
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Dashboard belum dikonfigurasi. Set DASHBOARD_PASSWORD_HASH dan DASHBOARD_SESSION_SECRET.", "csrf_token": ""},
            status_code=503,
        )
    if login_limited(request):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Terlalu banyak percobaan login. Coba lagi beberapa menit lagi.", "csrf_token": ""},
            status_code=429,
        )
    if not csrf_valid(csrf_token, request):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Sesi form tidak valid. Muat ulang halaman login.", "csrf_token": ""},
            status_code=403,
        )
    if len(password) < settings.dashboard_min_password_length:
        record_failed_login(request)
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Password minimal 8 karakter.", "csrf_token": ""},
            status_code=400,
        )
    try:
        password_ok = pwd.verify(password, settings.dashboard_password_hash)
    except (ValueError, TypeError):
        password_ok = False
    if not password_ok:
        record_failed_login(request)
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Password salah.", "csrf_token": ""},
            status_code=401,
        )
    clear_failed_logins(request)
    response = RedirectResponse("/", status_code=303)
    session = session_serializer.dumps({"authenticated": True, "nonce": secrets.token_urlsafe(24)})
    response.set_cookie(
        "dashboard_session", session, httponly=True,
        secure=settings.dashboard_cookie_secure, samesite="strict",
        max_age=settings.dashboard_session_max_age_seconds, path="/",
    )
    response.delete_cookie("dashboard_preauth", path="/")
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not require_auth(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"csrf_token": csrf_for_request(request), "environment": "NOT CONNECTED"},
    )


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not require_auth(request):
        return RedirectResponse("/login", status_code=303)
    if not csrf_valid(csrf_token, request):
        return HTMLResponse("Invalid CSRF token", status_code=403)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("dashboard_session", path="/")
    response.delete_cookie("dashboard_preauth", path="/")
    return response
