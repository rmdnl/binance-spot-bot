from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from decimal import Decimal

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db, init_db
from .grid import build_grid
from .models import RiskState, SpotOrder

BASE = Path(__file__).resolve().parent
settings = get_settings()
app = FastAPI(title="Binance Spot Bot", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def migrate_database_before_serving() -> None:
    """Apply pending schema migrations before the app serves requests."""
    init_db()
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
session_serializer = URLSafeTimedSerializer(settings.dashboard_session_secret or "deny-all-session", salt="spot-session-v1")
csrf_serializer = URLSafeTimedSerializer(settings.dashboard_session_secret or "deny-all-csrf", salt="spot-csrf-v1")
attempts = defaultdict(deque)


def configured():
    return bool(settings.dashboard_password_hash and len(settings.dashboard_session_secret) >= 32)


def client_key(r):
    return r.client.host if r.client else "unknown"


def limited(r):
    now = time.monotonic(); q = attempts[client_key(r)]
    while q and now - q[0] > settings.dashboard_login_window_seconds:
        q.popleft()
    return len(q) >= settings.dashboard_login_max_attempts


def csrf_valid(token, r):
    try: payload = csrf_serializer.loads(token, max_age=3600)
    except (BadSignature, SignatureExpired): return False
    session = r.cookies.get("dashboard_session", ""); pre = r.cookies.get("dashboard_preauth", "")
    expected = hashlib.sha256(session.encode()).hexdigest() if session else ""
    return hmac.compare_digest(payload.get("session", ""), expected) if session else bool(pre) and hmac.compare_digest(payload.get("nonce", ""), pre)


def auth(r):
    if not configured(): return False
    try: return session_serializer.loads(r.cookies.get("dashboard_session", ""), max_age=settings.dashboard_session_max_age_seconds).get("authenticated") is True
    except (BadSignature, SignatureExpired): return False


def require_auth(r):
    if not auth(r): raise HTTPException(401, "Authentication required")


def csrf(r):
    session = r.cookies.get("dashboard_session", "")
    payload = {"session": hashlib.sha256(session.encode()).hexdigest()} if session else {"nonce": r.cookies.get("dashboard_preauth", "")}
    return csrf_serializer.dumps(payload)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/login", response_class=HTMLResponse)
def login_page(r: Request):
    nonce = r.cookies.get("dashboard_preauth") or secrets.token_urlsafe(32)
    response = templates.TemplateResponse(r, "login.html", {"error": None, "csrf_token": csrf_serializer.dumps({"nonce": nonce})})
    response.set_cookie("dashboard_preauth", nonce, httponly=True, secure=settings.dashboard_cookie_secure, samesite="strict", max_age=900, path="/")
    return response

@app.post("/login", response_class=HTMLResponse)
def login(r: Request, password: str = Form(...), csrf_token: str = Form(...)):
    if not configured(): return templates.TemplateResponse(r, "login.html", {"error": "Dashboard belum dikonfigurasi.", "csrf_token": ""}, status_code=503)
    if limited(r): return templates.TemplateResponse(r, "login.html", {"error": "Terlalu banyak percobaan login.", "csrf_token": ""}, status_code=429)
    if not csrf_valid(csrf_token, r): return templates.TemplateResponse(r, "login.html", {"error": "Form tidak valid. Muat ulang.", "csrf_token": ""}, status_code=403)
    if len(password) < settings.dashboard_min_password_length or not pwd.verify(password, settings.dashboard_password_hash):
        attempts[client_key(r)].append(time.monotonic())
        return templates.TemplateResponse(r, "login.html", {"error": "Password salah atau terlalu pendek.", "csrf_token": ""}, status_code=401)
    attempts.pop(client_key(r), None); response = RedirectResponse("/", 303)
    response.set_cookie("dashboard_session", session_serializer.dumps({"authenticated": True, "nonce": secrets.token_urlsafe(24)}), httponly=True, secure=settings.dashboard_cookie_secure, samesite="strict", max_age=settings.dashboard_session_max_age_seconds, path="/")
    response.delete_cookie("dashboard_preauth", path="/"); return response

@app.post("/logout")
def logout(r: Request, csrf_token: str = Form(...)):
    if not auth(r): return RedirectResponse("/login", 303)
    if not csrf_valid(csrf_token, r): raise HTTPException(403, "Invalid CSRF token")
    response = RedirectResponse("/login", 303); response.delete_cookie("dashboard_session", path="/"); return response

@app.get("/", response_class=HTMLResponse)
def dashboard(r: Request):
    if not auth(r): return RedirectResponse("/login", 303)
    return templates.TemplateResponse(r, "dashboard.html", {"csrf_token": csrf(r), "environment": settings.default_environment.upper()})

@app.get("/api/account")
def account(r: Request):
    require_auth(r)
    from .exchange import BinanceSpot
    from .models import Environment
    try:
        ex = BinanceSpot(Environment(settings.default_environment)); raw = ex.balance(); rows = []
        for asset, total in raw.get("total", {}).items():
            if float(total or 0) > 0: rows.append({"asset": asset, "free": str(raw.get("free", {}).get(asset, 0)), "used": str(raw.get("used", {}).get(asset, 0)), "total": str(total)})
        trades = ex.recent_trades(limit=25)
        return {"available": True, "balances": rows, "trades": trades}
    except Exception as exc: return {"available": False, "error": str(exc), "balances": [], "trades": []}

@app.get("/api/orders")
def orders(r: Request, db: Session = Depends(get_db)):
    require_auth(r); rows = db.query(SpotOrder).order_by(SpotOrder.updated_at.desc()).limit(100).all()
    return [{"id": x.id, "symbol": x.symbol, "side": x.side, "type": x.order_type, "status": x.status, "amount": str(x.amount), "filled": str(x.filled_amount), "price": str(x.price) if x.price is not None else None, "exchange_order_id": x.exchange_order_id} for x in rows]

@app.post("/api/grid/preview")
def grid_preview(r: Request, symbol: str, lower_price: str, upper_price: str, grids: int, grid_type: str = "arithmetic"):
    require_auth(r); preview = build_grid(Decimal(lower_price), Decimal(upper_price), grids, grid_type)
    return {"symbol": symbol, "grid_type": preview.grid_type, "step_pct": str(preview.step_pct), "levels": [{"index": x.index, "price": str(x.price)} for x in preview.levels]}

@app.get("/api/risk/status")
def risk_status(r: Request, db: Session = Depends(get_db)):
    require_auth(r); state = db.get(RiskState, 1)
    return {"status": "halted" if state and state.halted else "ready", "equity_quote": "0", "drawdown_pct": "0", "daily_realized_pnl_quote": "0", "reason": state.reason if state else None}

from .trading_routes import register_trading_routes
register_trading_routes(app)
