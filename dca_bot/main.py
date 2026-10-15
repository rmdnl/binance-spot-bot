from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from decimal import Decimal as D
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
from .models import AuditEvent, PaperDealRecord, StrategyRecord, StrategyRiskState
from .strategy import StrategyConfig
from .dca_math import build_dca_summary
from .market_data import BinanceSpotMarketData
from .paper_engine import deserialize_paper_deal, estimate_unrealized_pnl_quote, serialize_paper_deal, start_paper_deal
from .worker import run_paper_worker, tick_active_paper_deals
from .risk import reserved_capital_quote, strategy_risk_status
from .entry_guard import serialized_entry
from .binance_account import BinanceAccountData

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.assert_safe()
    init_db()
    stop_event = asyncio.Event()
    worker_task = None
    if settings.trading_mode.lower() == "paper":
        worker_task = asyncio.create_task(
            run_paper_worker(stop_event, settings.paper_poll_interval_seconds),
            name="dca-deal-worker",
        )
    try:
        yield
    finally:
        if worker_task is not None:
            stop_event.set()
            await worker_task


app = FastAPI(
    title="DCA Bot",
    version="0.4.0",
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.session_cookie_secure,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def ensure_authenticated(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")


def get_or_create_config(db: Session) -> StrategyRecord:
    """There is exactly one dashboard-owned DCA configuration."""
    record = db.query(StrategyRecord).order_by(StrategyRecord.id.asc()).first()
    if record is None:
        config = StrategyConfig()
        record = StrategyRecord(name="DCA Configuration", config_json=config.model_dump_json(), enabled=True)
        db.add(record)
        db.commit()
        db.refresh(record)
    return record


def _account_snapshot(config: StrategyConfig) -> dict:
    """Read-only Binance account snapshot; failures never break the dashboard."""
    if settings.trading_mode.lower().strip() != "testnet":
        return {"available": False, "error": "Binance account view is available in testnet mode only."}
    try:
        account = BinanceAccountData(settings.binance_api_key, settings.binance_api_secret, testnet=settings.binance_testnet)
        symbols = [BinanceSpotMarketData.normalize_symbol(x) for x in config.symbols]
        balances = account.balances()
        trades = account.recent_trades(symbols)
        pnl = account.pnl(symbols)
        return {
            "available": True,
            "balances": [{"asset": x.asset, "free": str(x.free), "used": str(x.used), "total": str(x.total)} for x in balances],
            "trades": [{"symbol": x.symbol, "side": x.side, "amount": str(x.amount), "price": str(x.price), "cost": str(x.cost), "fee": str(x.fee), "fee_currency": x.fee_currency, "timestamp": x.timestamp, "order_id": x.order_id} for x in trades],
            "pnl": [{"symbol": x.symbol, "base": x.base, "quote": x.quote, "realized": str(x.realized), "unrealized": str(x.unrealized), "quantity": str(x.quantity), "cost_basis": str(x.cost_basis), "mark_price": str(x.mark_price)} for x in pnl],
            "note": "PNL is calculated per configured pair from fetched Binance trade history; values are not combined across different quote currencies.",
        }
    except Exception as exc:
        return {"available": False, "error": f"Binance account data unavailable: {exc}"}


def render_dashboard(request: Request, db: Session, *, saved: bool = False, error: str | None = None, status_code: int = 200):
    record = get_or_create_config(db)
    config = StrategyConfig.model_validate_json(record.config_json)
    rows = db.query(PaperDealRecord).order_by(PaperDealRecord.created_at.desc()).limit(50).all()
    deals = []
    for row in rows:
        try:
            deal = deserialize_paper_deal(row.state_json)
            snapshot = deal.snapshot()
            snapshot["unrealized_pnl_quote"] = str(estimate_unrealized_pnl_quote(deal) if deal.status == "active" else D("0"))
            deals.append(snapshot)
        except Exception:
            continue
    account = _account_snapshot(config)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"config": config.model_dump(), "config_id": record.id, "deals": deals, "account": account, "csrf_token": csrf_token(request), "saved": saved, "error": error, "mode": settings.trading_mode},
        status_code=status_code,
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": settings.trading_mode, "autonomous": "disabled"}


@app.get("/api/account")
def account_dashboard(request: Request, db: Session = Depends(get_db)):
    ensure_authenticated(request)
    record = get_or_create_config(db)
    config = StrategyConfig.model_validate_json(record.config_json)
    return _account_snapshot(config)


@app.get("/api/market/ticker")
def market_ticker(request: Request, symbol: str = "BTC/USDT"):
    ensure_authenticated(request)
    try:
        ticker = BinanceSpotMarketData().ticker(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Binance Spot market data is unavailable") from exc
    return {"symbol": ticker.symbol, "last": str(ticker.last), "bid": str(ticker.bid) if ticker.bid is not None else None, "ask": str(ticker.ask) if ticker.ask is not None else None, "timestamp_ms": ticker.timestamp_ms, "market": "spot"}


@app.get("/api/dca/preview")
def dca_preview(request: Request, base_price: str, base_order_quote: str = "10", safety_order_quote: str = "10", safety_order_count: int = 4, initial_deviation_pct: str = "1", deviation_scale: str = "1", safety_volume_scale: str = "1", averaging_method: str = "fixed_order_value", safety_order_base_quantity: str = "0.001", dca_mode: str = "position_averaging"):
    ensure_authenticated(request)
    from decimal import Decimal, InvalidOperation
    try:
        summary = build_dca_summary(base_price=Decimal(base_price), base_order_quote=Decimal(base_order_quote), safety_order_quote=Decimal(safety_order_quote), safety_order_count=safety_order_count, initial_deviation_pct=Decimal(initial_deviation_pct), deviation_scale=Decimal(deviation_scale), safety_volume_scale=Decimal(safety_volume_scale), averaging_method=averaging_method, safety_order_base_quantity=Decimal(safety_order_base_quantity), dca_mode=dca_mode)
    except (ValueError, InvalidOperation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"base_price": str(summary.base_price), "base_order_quote": str(summary.base_quote_amount), "base_quantity": str(summary.base_quantity), "safety_orders": [{"index": x.index, "deviation_pct": str(x.deviation_pct), "price": str(x.price), "quote_amount": str(x.quote_amount), "base_quantity": str(x.base_quantity)} for x in summary.safety_orders], "total_quote_required": str(summary.total_quote_required), "total_base_quantity": str(summary.total_base_quantity), "average_entry_price": str(summary.average_entry_price), "deepest_deviation_pct": str(summary.deepest_deviation_pct), "average_entry_deviation_pct": str(summary.average_entry_deviation_pct), "execution": settings.trading_mode}


@app.get("/api/dca/deals")
def dca_deals(request: Request, db: Session = Depends(get_db)):
    ensure_authenticated(request)
    rows = db.query(PaperDealRecord).order_by(PaperDealRecord.created_at.desc()).all()
    result = []
    for row in rows:
        deal = deserialize_paper_deal(row.state_json)
        if row.status != deal.status:
            raise HTTPException(status_code=503, detail="Persisted DCA deal state is inconsistent")
        snapshot = deal.snapshot()
        snapshot["unrealized_pnl_quote"] = str(estimate_unrealized_pnl_quote(deal) if deal.status == "active" else D("0"))
        result.append(snapshot)
    return {"deals": result, "execution": settings.trading_mode}


@app.post("/api/dca/deals/start")
@serialized_entry
def dca_deal_start(request: Request, csrf: str = Form(..., alias="csrf_token"), symbol: str = Form(...), db: Session = Depends(get_db)):
    ensure_authenticated(request)
    check_csrf(request, csrf)
    record = get_or_create_config(db)
    try:
        config = StrategyConfig.model_validate_json(record.config_json)
        normalized = BinanceSpotMarketData.normalize_symbol(symbol)
        if normalized not in config.symbols:
            raise ValueError("Pair is not in the manually configured pair list")
        risk_status = strategy_risk_status(db, record)
        if risk_status["blocked"]:
            raise ValueError(str(risk_status["reason"]))
        active_rows = db.query(PaperDealRecord).filter_by(status="active").all()
        active_deals = [deserialize_paper_deal(row.state_json) for row in active_rows]
        if len(active_deals) >= config.max_active_deals:
            raise ValueError("Maximum active DCA deals reached")
        if sum(1 for deal in active_deals if deal.symbol == normalized) >= config.max_active_deals_per_symbol:
            raise ValueError("This pair already has an active DCA deal")
        if reserved_capital_quote(active_deals) + D(str(config.max_capital_per_deal)) > D(str(config.max_total_allocation)):
            raise ValueError("Total allocation cap would be exceeded")
        ticker = BinanceSpotMarketData().ticker(normalized)
        current = ticker.last
        if config.trigger_price is not None and current > D(str(config.trigger_price)):
            raise ValueError(f"Trigger Price not reached; current price is {current}")
        if config.price_range_enabled and not (D(str(config.price_range_min)) <= current <= D(str(config.price_range_max))):
            raise ValueError("Current market price is outside the configured Price Range")
        deal = start_paper_deal(config, normalized, current)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="DCA entry blocked because market/risk state is unavailable") from exc
    db.add(PaperDealRecord(id=deal.id, strategy_id=record.id, symbol=deal.symbol, status=deal.status, state_json=serialize_paper_deal(deal)))
    db.add(AuditEvent(event_type="dca_deal_started", detail=json.dumps({"deal_id": deal.id, "symbol": deal.symbol, "price": str(deal.base_price)})))
    db.commit()
    return {"deal": deal.snapshot(), "execution": settings.trading_mode, "orders_sent_to_exchange": False}


@app.post("/api/dca/deals/tick")
def dca_deal_tick(request: Request, csrf: str = Form(..., alias="csrf_token"), db: Session = Depends(get_db)):
    ensure_authenticated(request)
    check_csrf(request, csrf)
    return {**tick_active_paper_deals(db), "execution": settings.trading_mode, "orders_sent_to_exchange": False}


@app.get("/api/risk/status")
def dca_risk_status(request: Request, db: Session = Depends(get_db)):
    ensure_authenticated(request)
    record = get_or_create_config(db)
    try:
        status = strategy_risk_status(db, record)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Risk evaluation failed closed") from exc
    return {"status": "blocked" if status["blocked"] else "clear", **status, "execution": settings.trading_mode}


@app.post("/api/risk/reset-drawdown")
def reset_drawdown_guard(request: Request, csrf: str = Form(..., alias="csrf_token"), db: Session = Depends(get_db)):
    ensure_authenticated(request)
    check_csrf(request, csrf)
    record = get_or_create_config(db)
    try:
        status = strategy_risk_status(db, record)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Risk state unavailable; reset refused") from exc
    if status["daily_loss_tripped"]:
        raise HTTPException(status_code=409, detail="Daily loss limit remains tripped")
    state = db.get(StrategyRiskState, record.id)
    if state is None:
        raise HTTPException(status_code=409, detail="Risk state is not initialized")
    state.drawdown_tripped = False
    state.drawdown_trip_reason = None
    state.peak_equity_quote = str(status["equity_quote"])
    db.add(AuditEvent(event_type="risk_drawdown_reset", detail=json.dumps({"config_id": record.id, "equity_quote": status["equity_quote"]})))
    db.commit()
    return {"drawdown_tripped": False, "peak_equity_quote": state.peak_equity_quote}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None, "csrf_token": csrf_token(request)})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...), csrf: str = Form(..., alias="csrf_token")):
    check_csrf(request, csrf)
    if not verify_password(password, settings.admin_password_hash):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid credentials or dashboard not configured.", "csrf_token": csrf_token(request)}, status_code=401)
    request.session.clear()
    request.session["authenticated"] = True
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(..., alias="csrf_token")):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), saved: bool = False):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return render_dashboard(request, db, saved=saved)


@app.post("/dca/save", response_class=HTMLResponse)
def save_dca(
    request: Request, csrf: str = Form(..., alias="csrf_token"), symbols: str = Form("BTCUSDT"),
    base_order_quote: float = Form(10), safety_order_quote: float = Form(10), max_safety_orders: int = Form(4),
    take_profit_pct: float = Form(1), take_profit_basis: str = Form("average_entry"), initial_deviation_pct: float = Form(1),
    deviation_scale: float = Form(1.0), safety_order_volume_scale: float = Form(1.0), dca_mode: str = Form("position_averaging"),
    averaging_method: str = Form("fixed_order_value"), safety_order_base_quantity: float = Form(0.001),
    trigger_price: float | None = Form(None), price_range_enabled: bool = Form(False), price_range_min: float | None = Form(None), price_range_max: float | None = Form(None), cooldown_seconds: int = Form(0),
    trailing_take_profit_enabled: bool = Form(False), trailing_activation_pct: float = Form(1), trailing_callback_pct: float = Form(0.3),
    stop_loss_enabled: bool = Form(False), stop_loss_pct: float = Form(10), stop_loss_basis: str = Form("base_order"), end_bot_on_stop_loss: bool = Form(False), move_stop_to_breakeven_enabled: bool = Form(False),
    breakeven_activation_pct: float = Form(1), breakeven_offset_pct: float = Form(0), reinvest_profit: bool = Form(False), max_deal_duration_hours: int = Form(0), max_active_deals: int = Form(3),
    max_active_deals_per_symbol: int = Form(1), max_capital_per_deal: float = Form(200), max_total_allocation: float = Form(600), reserve_balance: float = Form(20), daily_loss_limit_quote: float = Form(25), max_equity_drawdown_pct: float = Form(5),
    min_net_profit_pct: float = Form(0.3), fee_rate_pct_per_side: float = Form(0.1), slippage_pct_per_side: float = Form(0.05), db: Session = Depends(get_db),
):
    ensure_authenticated(request)
    check_csrf(request, csrf)
    try:
        normalized_symbols = [x.strip() for x in symbols.split(",") if x.strip()]
        config = StrategyConfig(
            name="DCA Configuration", symbols=normalized_symbols, pair_selection_mode="manual", base_order_quote=base_order_quote, safety_order_quote=safety_order_quote, max_safety_orders=max_safety_orders,
            take_profit_pct=take_profit_pct, take_profit_basis=take_profit_basis, initial_deviation_pct=initial_deviation_pct, deviation_scale=deviation_scale, safety_order_volume_scale=safety_order_volume_scale, dca_mode=dca_mode,
            averaging_method=averaging_method, safety_order_base_quantity=safety_order_base_quantity, trigger_price=trigger_price, price_range_enabled=price_range_enabled, price_range_min=price_range_min, price_range_max=price_range_max, cooldown_seconds=cooldown_seconds,
            trailing_take_profit_enabled=trailing_take_profit_enabled, trailing_activation_pct=trailing_activation_pct, trailing_callback_pct=trailing_callback_pct, stop_loss_enabled=stop_loss_enabled, stop_loss_pct=stop_loss_pct, stop_loss_basis=stop_loss_basis, end_bot_on_stop_loss=end_bot_on_stop_loss,
            move_stop_to_breakeven_enabled=move_stop_to_breakeven_enabled, breakeven_activation_pct=breakeven_activation_pct, breakeven_offset_pct=breakeven_offset_pct, reinvest_profit=reinvest_profit, max_deal_duration_hours=max_deal_duration_hours,
            max_active_deals=max_active_deals, max_active_deals_per_symbol=max_active_deals_per_symbol, max_capital_per_deal=max_capital_per_deal, max_total_allocation=max_total_allocation, reserve_balance=reserve_balance,
            daily_loss_limit_quote=daily_loss_limit_quote, max_equity_drawdown_pct=max_equity_drawdown_pct, min_net_profit_pct=min_net_profit_pct, fee_rate_pct_per_side=fee_rate_pct_per_side, slippage_pct_per_side=slippage_pct_per_side,
        )
        if config.estimated_net_tp_pct() < config.min_net_profit_pct:
            raise ValueError("Net take-profit estimate is below the configured minimum")
        if config.estimated_max_deal_capital() > config.max_capital_per_deal:
            raise ValueError("DCA ladder capital exceeds max capital per deal")
        if config.estimated_max_deal_capital() * config.max_active_deals > config.max_total_allocation:
            raise ValueError("Estimated DCA capital multiplied by active deals exceeds total allocation")
    except Exception as exc:
        return render_dashboard(request, db, error=str(exc), status_code=422)
    record = get_or_create_config(db)
    record.name = "DCA Configuration"
    record.config_json = config.model_dump_json()
    record.enabled = True
    db.add(AuditEvent(event_type="dca_config_saved", detail=json.dumps({"config_id": record.id, "symbols": config.symbols})))
    db.commit()
    return RedirectResponse("/?saved=true", status_code=303)
