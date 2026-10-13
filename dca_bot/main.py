from __future__ import annotations
import json
import secrets
from pathlib import Path
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from .auth import is_authenticated, verify_password
from .config import get_settings
from .db import get_db, init_db
from .models import AuditEvent, StrategyRecord
from .strategy import StrategyConfig

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()
app = FastAPI(title='DCA Bot', version='0.1.0', docs_url=None if settings.app_env == 'production' else '/docs', redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, max_age=settings.session_max_age_seconds, same_site='lax', https_only=settings.session_cookie_secure)
app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
templates = Jinja2Templates(directory=BASE_DIR / 'templates')


@app.on_event('startup')
def startup() -> None:
    settings.assert_safe()
    init_db()


def csrf_token(request: Request) -> str:
    token = request.session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        request.session['csrf_token'] = token
    return token


def check_csrf(request: Request, token: str) -> None:
    expected = request.session.get('csrf_token', '')
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail='Invalid CSRF token')


@app.get('/healthz')
def healthz() -> dict[str, str]:
    return {'status': 'ok', 'mode': settings.trading_mode, 'execution': 'disabled'}


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse('/', status_code=303)
    return templates.TemplateResponse(request, 'login.html', {'error': None, 'csrf_token': csrf_token(request)})


@app.post('/login', response_class=HTMLResponse)
def login(request: Request, password: str = Form(...), csrf: str = Form(..., alias='csrf_token')):
    check_csrf(request, csrf)
    if not verify_password(password, settings.admin_password_hash):
        return templates.TemplateResponse(request, 'login.html', {'error': 'Invalid credentials or dashboard not configured.', 'csrf_token': csrf_token(request)}, status_code=401)
    request.session.clear()
    request.session['authenticated'] = True
    request.session['csrf_token'] = secrets.token_urlsafe(32)
    return RedirectResponse('/', status_code=303)


@app.post('/logout')
def logout(request: Request, csrf: str = Form(..., alias='csrf_token')):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse('/login', status_code=303)


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), saved: bool = False):
    if not is_authenticated(request):
        return RedirectResponse('/login', status_code=303)
    rows = db.query(StrategyRecord).order_by(StrategyRecord.id.desc()).all()
    strategies = []
    for row in rows:
        try:
            config = StrategyConfig.model_validate_json(row.config_json)
            strategies.append({'id': row.id, 'name': config.name, 'symbols': config.symbols, 'enabled': row.enabled})
        except ValueError:
            continue
    return templates.TemplateResponse(request, 'dashboard.html', {'strategies': strategies, 'csrf_token': csrf_token(request), 'saved': saved})


@app.post('/strategies/save')
def save_strategy(request: Request, csrf: str = Form(..., alias='csrf_token'), strategy_id: str = Form(''), name: str = Form(...), symbols: str = Form(...), base_order_quote: float = Form(...), safety_order_quote: float = Form(...), max_safety_orders: int = Form(...), take_profit_pct: float = Form(...), initial_deviation_pct: float = Form(...), max_active_deals: int = Form(...), order_size_mode: str = Form('fixed_quote'), base_order_balance_pct: float = Form(1), safety_order_volume_scale: float = Form(1.5), deviation_scale: float = Form(1.5), trailing_take_profit_enabled: bool = Form(False), trailing_activation_pct: float = Form(1), trailing_callback_pct: float = Form(0.3), stop_loss_enabled: bool = Form(False), stop_loss_pct: float = Form(10), entry_filter: str = Form('manual'), timeframe: str = Form('1h'), ema_fast: int = Form(50), ema_slow: int = Form(200), rsi_period: int = Form(14), rsi_max_entry: float = Form(40), adx_min: float = Form(20), atr_period: int = Form(14), cooldown_seconds: int = Form(300), order_timeout_seconds: int = Form(60), max_capital_per_deal: float = Form(200), max_total_allocation: float = Form(600), reserve_balance: float = Form(20), daily_loss_limit_quote: float = Form(25), max_equity_drawdown_pct: float = Form(5), min_net_profit_pct: float = Form(0.3), fee_rate_pct_per_side: float = Form(0.1), slippage_pct_per_side: float = Form(0.05), retry_limit: int = Form(3), reconciliation_seconds: int = Form(30), order_type: str = Form('limit'), db: Session = Depends(get_db)):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail='Authentication required')
    check_csrf(request, csrf)
    try:
        config = StrategyConfig(name=name, symbols=[s.strip() for s in symbols.split(',') if s.strip()], base_order_quote=base_order_quote, safety_order_quote=safety_order_quote, max_safety_orders=max_safety_orders, take_profit_pct=take_profit_pct, initial_deviation_pct=initial_deviation_pct, max_active_deals=max_active_deals, order_size_mode=order_size_mode, base_order_balance_pct=base_order_balance_pct, safety_order_volume_scale=safety_order_volume_scale, deviation_scale=deviation_scale, trailing_take_profit_enabled=trailing_take_profit_enabled, trailing_activation_pct=trailing_activation_pct, trailing_callback_pct=trailing_callback_pct, stop_loss_enabled=stop_loss_enabled, stop_loss_pct=stop_loss_pct, entry_filter=entry_filter, timeframe=timeframe, ema_fast=ema_fast, ema_slow=ema_slow, rsi_period=rsi_period, rsi_max_entry=rsi_max_entry, adx_min=adx_min, atr_period=atr_period, cooldown_seconds=cooldown_seconds, order_timeout_seconds=order_timeout_seconds, max_capital_per_deal=max_capital_per_deal, max_total_allocation=max_total_allocation, reserve_balance=reserve_balance, daily_loss_limit_quote=daily_loss_limit_quote, max_equity_drawdown_pct=max_equity_drawdown_pct, min_net_profit_pct=min_net_profit_pct, fee_rate_pct_per_side=fee_rate_pct_per_side, slippage_pct_per_side=slippage_pct_per_side, retry_limit=retry_limit, reconciliation_seconds=reconciliation_seconds, order_type=order_type)
        required_capital = config.estimated_max_deal_capital()
        if required_capital > config.max_capital_per_deal:
            raise ValueError(f'Estimated deal capital ({required_capital:.2f} USDT) exceeds max capital per deal ({config.max_capital_per_deal:.2f} USDT).')
        if config.estimated_net_tp_pct() < config.min_net_profit_pct:
            raise ValueError('Estimated net take profit is below the configured minimum after fee and slippage assumptions.')
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if strategy_id:
        try:
            record = db.get(StrategyRecord, int(strategy_id))
        except ValueError:
            record = None
        if record is None:
            raise HTTPException(status_code=404, detail='Strategy not found')
    else:
        record = StrategyRecord(name=config.name, config_json=config.model_dump_json(), enabled=False)
        db.add(record)
    record.name = config.name
    record.config_json = config.model_dump_json()
    record.enabled = False
    db.add(AuditEvent(event_type='strategy_saved', detail=json.dumps({'id': record.id, 'name': config.name, 'symbols': config.symbols})))
    db.commit()
    return RedirectResponse('/?saved=true#strategies', status_code=303)
