from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .auth import is_authenticated, verify_password
from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .models import AuditEvent, PaperDealRecord, StrategyRecord, StrategyRiskState
from .strategy import StrategyConfig
from .dca_math import build_dca_summary
from .market_data import BinanceSpotMarketData
from .autonomous import analyze_market
from decimal import Decimal as D
from .worker import run_paper_worker, tick_active_paper_deals
from .entry_guard import serialized_entry
from .risk import global_capital_status, reserved_capital_quote, strategy_risk_status
from .paper_engine import (
    deserialize_paper_deal,
    estimate_unrealized_pnl_quote,
    serialize_paper_deal,
    start_paper_deal,
    update_paper_deal,
)

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
            name="dca-paper-worker",
        )
    try:
        yield
    finally:
        if worker_task is not None:
            stop_event.set()
            await worker_task


app = FastAPI(
    title="DCA Bot",
    version="0.1.1",
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


def estimate_profile_capital(config: StrategyConfig, reference_price: float | None) -> tuple[float | None, float]:
    """Estimate profile capital, deferring dynamic fixed-coin sizing to each discovered market.

    Automatic USDT discovery uses adaptive DCA parameters per candidate. A profile-level
    estimate must not reject a ladder that the autonomous analyzer can safely shrink to fit
    the configured per-deal cap. The scanner validates each candidate's complete ladder
    against its own price before it can open a paper deal.
    """
    if config.pair_selection_mode == "automatic_usdt":
        return None, config.max_capital_per_deal * config.max_active_deals
    required = config.estimated_max_deal_capital(reference_price)
    return required, config.estimated_total_capacity(reference_price)


def render_dashboard(request: Request, db: Session, *, saved: bool = False, error: str | None = None, status_code: int = 200):
    rows = db.query(StrategyRecord).order_by(StrategyRecord.id.desc()).all()
    strategies = []
    for row in rows:
        try:
            config = StrategyConfig.model_validate_json(row.config_json)
            strategies.append({
                "id": row.id,
                "name": config.name,
                "symbols": (["Automatic USDT discovery"] if config.pair_selection_mode == "automatic_usdt" else config.symbols),
                "enabled": row.enabled,
                "config": config.model_dump(),
            })
        except ValueError:
            continue
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "strategies": strategies,
            "csrf_token": csrf_token(request),
            "saved": saved,
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": settings.trading_mode, "execution": "disabled"}


@app.get("/api/market/ticker")
def market_ticker(request: Request, symbol: str = "BTC/USDT"):
    """Authenticated public Binance Spot ticker lookup. Never uses trading credentials."""
    ensure_authenticated(request)
    try:
        ticker = BinanceSpotMarketData().ticker(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Binance public market data is temporarily unavailable") from exc
    return {
        "symbol": ticker.symbol,
        "last": str(ticker.last),
        "bid": str(ticker.bid) if ticker.bid is not None else None,
        "ask": str(ticker.ask) if ticker.ask is not None else None,
        "timestamp_ms": ticker.timestamp_ms,
        "market": "spot",
    }


@app.get("/api/strategy/analyze")
def strategy_analyze(
    request: Request,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    strategy_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Analyze public Spot candles and suggest adaptive DCA parameters; never places orders."""
    ensure_authenticated(request)
    try:
        normalized = BinanceSpotMarketData.normalize_symbol(symbol)
        if strategy_id is not None:
            record = db.get(StrategyRecord, strategy_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            config = StrategyConfig.model_validate_json(record.config_json)
            if normalized not in config.symbols:
                raise HTTPException(status_code=422, detail="Pair is not included in this strategy")
        else:
            config = StrategyConfig.model_validate({"symbols": [normalized], "timeframe": timeframe})
        if timeframe not in {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}:
            raise ValueError("Unsupported Binance Spot candle timeframe")
        config = config.model_copy(update={"timeframe": timeframe})
        limit = min(1000, max(config.ema_slow + 50, config.atr_period * 3 + 10, 300))
        candles = BinanceSpotMarketData().candles(normalized, timeframe, limit=limit)
        if len(candles) < 2:
            raise ValueError("Not enough Spot candles to identify the latest closed candle")
        closed_candles = candles[:-1]
        timeframe_seconds = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900,
            "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
            "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
        }[timeframe]
        try:
            latest_closed_ms = int(closed_candles[-1][0])
        except (TypeError, ValueError, OverflowError, IndexError) as exc:
            raise ValueError("Latest closed candle has an invalid timestamp") from exc
        candle_age_seconds = time.time() - (latest_closed_ms / 1000)
        if candle_age_seconds < -60 or candle_age_seconds > timeframe_seconds * 2:
            raise ValueError("Latest closed candle is stale or has a future timestamp")
        decision = analyze_market(normalized, timeframe, closed_candles, config)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Binance Spot candle data is temporarily unavailable") from exc
    return decision.to_dict()


@app.get("/api/dca/preview")
def dca_preview(
    request: Request,
    base_price: str,
    base_order_quote: str = "10",
    safety_order_quote: str = "10",
    safety_order_count: int = 4,
    initial_deviation_pct: str = "1",
    deviation_scale: str = "1",
    safety_volume_scale: str = "1",
    averaging_method: str = "fixed_order_value",
    safety_order_base_quantity: str = "0.001",
    dca_mode: str = "position_averaging",
):
    """Authenticated deterministic DCA ladder and capital preview; no orders are placed."""
    ensure_authenticated(request)
    from decimal import Decimal, InvalidOperation
    try:
        summary = build_dca_summary(
            base_price=Decimal(base_price),
            base_order_quote=Decimal(base_order_quote),
            safety_order_quote=Decimal(safety_order_quote),
            safety_order_count=safety_order_count,
            initial_deviation_pct=Decimal(initial_deviation_pct),
            deviation_scale=Decimal(deviation_scale),
            safety_volume_scale=Decimal(safety_volume_scale),
            averaging_method=averaging_method,
            safety_order_base_quantity=Decimal(safety_order_base_quantity),
            dca_mode=dca_mode,
        )
    except (ValueError, InvalidOperation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "base_price": str(summary.base_price),
        "base_order_quote": str(summary.base_quote_amount),
        "base_quantity": str(summary.base_quantity),
        "safety_orders": [
            {
                "index": level.index,
                "deviation_pct": str(level.deviation_pct),
                "price": str(level.price),
                "quote_amount": str(level.quote_amount),
                "base_quantity": str(level.base_quantity),
            }
            for level in summary.safety_orders
        ],
        "total_quote_required": str(summary.total_quote_required),
        "total_base_quantity": str(summary.total_base_quantity),
        "average_entry_price": str(summary.average_entry_price),
        "deepest_deviation_pct": str(summary.deepest_deviation_pct),
        "average_entry_deviation_pct": str(summary.average_entry_deviation_pct),
        "execution": "disabled",
    }


@app.get("/api/paper/deals")
def paper_deals(request: Request, db: Session = Depends(get_db)):
    ensure_authenticated(request)
    rows = db.query(PaperDealRecord).order_by(PaperDealRecord.created_at.desc()).all()
    result = []
    for row in rows:
        try:
            deal = deserialize_paper_deal(row.state_json)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(
                status_code=503,
                detail="Persisted paper deal state is unreadable; dashboard data is withheld for safety",
            ) from exc
        if row.id != deal.id:
            raise HTTPException(
                status_code=503,
                detail="Persisted paper deal identity is inconsistent; dashboard data is withheld for safety",
            )
        if row.status != deal.status:
            raise HTTPException(
                status_code=503,
                detail="Persisted paper deal status is inconsistent; dashboard data is withheld for safety",
            )
        if row.symbol != deal.symbol:
            raise HTTPException(
                status_code=503,
                detail="Persisted paper deal symbol is inconsistent; dashboard data is withheld for safety",
            )
        snapshot = deal.snapshot()
        if deal.status == "active":
            snapshot["unrealized_pnl_quote"] = str(
                estimate_unrealized_pnl_quote(deal)
            )
        else:
            snapshot["unrealized_pnl_quote"] = "0"
        result.append(snapshot)
    return {"deals": result, "execution": "paper"}


def persist_paper_entry(db: Session, strategy_id: int, deal) -> None:
    """Persist a paper deal and its audit event atomically or fail closed."""
    try:
        db.add(PaperDealRecord(
            id=deal.id,
            strategy_id=strategy_id,
            symbol=deal.symbol,
            status=deal.status,
            state_json=serialize_paper_deal(deal),
        ))
        db.add(AuditEvent(
            event_type="paper_deal_started",
            detail=json.dumps({
                "deal_id": deal.id,
                "strategy_id": strategy_id,
                "symbol": deal.symbol,
                "base_price": str(deal.base_price),
            }),
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Paper entry persistence could not be confirmed; inspect persisted deal state before retrying",
        ) from exc


@app.post("/api/paper/deals/start")
@serialized_entry
def paper_deal_start(
    request: Request,
    csrf: str = Form(..., alias="csrf_token"),
    strategy_id: int = Form(...),
    symbol: str = Form(...),
    db: Session = Depends(get_db),
):
    ensure_authenticated(request)
    check_csrf(request, csrf)
    record = db.get(StrategyRecord, strategy_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        risk_status = strategy_risk_status(db, record)
        if risk_status["blocked"]:
            db.add(AuditEvent(
                event_type="risk_entry_blocked",
                detail=json.dumps({"strategy_id": strategy_id, "reason": risk_status["reason"], "source": "manual"}),
            ))
            db.commit()
            raise HTTPException(status_code=409, detail=str(risk_status["reason"]))
        config = StrategyConfig.model_validate_json(record.config_json)
        normalized = BinanceSpotMarketData.normalize_symbol(symbol)
        active_rows = db.query(PaperDealRecord).filter_by(status="active").all()
        strategy_deals = []
        symbol_active = 0
        for active_row in active_rows:
            active_deal = deserialize_paper_deal(active_row.state_json)
            if active_row.strategy_id == strategy_id:
                strategy_deals.append(active_deal)
                if active_deal.symbol == normalized:
                    symbol_active += 1
        if len(strategy_deals) >= config.max_active_deals:
            raise ValueError("Maximum active deals for this strategy reached")
        if symbol_active >= config.max_active_deals_per_symbol:
            raise ValueError("Maximum active deals for this pair reached")
        reserved = reserved_capital_quote(strategy_deals)
        new_reservation = D(str(config.max_capital_per_deal))
        total_cap = D(str(config.max_total_allocation))
        if reserved + new_reservation > total_cap:
            raise ValueError(
                "Total allocation cap would be exceeded by active and new deal reservations"
            )
        global_status = global_capital_status(db, D(str(settings.global_capital_limit_quote)))
        if D(global_status["reserved_quote"]) + new_reservation > D(global_status["limit_quote"]):
            raise ValueError(
                "Global capital limit would be exceeded by active and new deal reservations across all strategies"
            )
        market_data = BinanceSpotMarketData()
        ticker = market_data.ticker(normalized)
        market_data.validate_minimum_order_sizes(normalized, config, ticker.last)
        deal = start_paper_deal(config, normalized, ticker.last)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Risk state or Spot market data unavailable; entry blocked") from exc
    persist_paper_entry(db, strategy_id, deal)
    return {"deal": deal.snapshot(), "execution": "paper", "orders_sent_to_exchange": False}


@app.get("/api/risk/status")
def paper_risk_status(request: Request, db: Session = Depends(get_db)):
    """Return paper-equity guard status for dashboard display; never touches exchange orders."""
    ensure_authenticated(request)
    result = []
    records = db.query(StrategyRecord).order_by(StrategyRecord.id.asc()).all()
    for record in records:
        try:
            status = strategy_risk_status(db, record)
            result.append({
                "strategy_id": record.id,
                "name": record.name,
                "status": "blocked" if status["blocked"] else "clear",
                **status,
            })
        except Exception:
            logging.exception("Risk status unavailable for strategy %s", record.id)
            result.append({
                "strategy_id": record.id,
                "name": record.name,
                "status": "error",
                "blocked": True,
                "reason": "Risk evaluation failed; new entries must remain blocked",
                "drawdown_tripped": None,
                "daily_loss_tripped": None,
            })
    try:
        shared_capital = global_capital_status(db, D(str(settings.global_capital_limit_quote)))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Risk status or global capital reservation could not be evaluated") from exc
    return {
        "global_capital": {
            "limit_quote": shared_capital["limit_quote"],
            "reserved_quote": shared_capital["reserved_quote"],
            "remaining_quote": shared_capital["remaining_quote"],
            "within_limit": shared_capital["within_limit"] == "true",
            "quote_asset": "USDT",
        },
        "risk_model": "virtual_paper_equity",
        "mark_source": "last persisted paper-deal price",
        "daily_loss_timezone": "UTC",
        "entries_enabled": (not any(item["blocked"] for item in result)) and shared_capital["within_limit"] == "true",
        "strategies": result,
        "execution": "paper",
    }


@app.post("/api/risk/reset-drawdown")
def reset_drawdown_guard(
    request: Request,
    csrf: str = Form(..., alias="csrf_token"),
    strategy_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Admin-only reset for the latched paper-equity drawdown breaker."""
    ensure_authenticated(request)
    check_csrf(request, csrf)
    record = db.get(StrategyRecord, strategy_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        status = strategy_risk_status(db, record)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Risk state unavailable; reset refused") from exc
    if status["daily_loss_tripped"]:
        db.commit()
        raise HTTPException(status_code=409, detail="Daily loss limit remains tripped; reset refused")
    state = db.get(StrategyRiskState, strategy_id)
    if state is None:
        db.commit()
        raise HTTPException(status_code=409, detail="Risk state is not initialized")
    state.drawdown_tripped = False
    state.drawdown_trip_reason = None
    state.peak_equity_quote = str(status["equity_quote"])
    db.add(AuditEvent(
        event_type="risk_drawdown_reset",
        detail=json.dumps({
            "strategy_id": strategy_id,
            "equity_quote": status["equity_quote"],
            "actor": "admin",
        }),
    ))
    db.commit()
    return {"strategy_id": strategy_id, "drawdown_tripped": False, "peak_equity_quote": state.peak_equity_quote}


@app.post("/api/paper/deals/tick")
def paper_deal_tick(
    request: Request,
    csrf: str = Form(..., alias="csrf_token"),
    db: Session = Depends(get_db),
):
    ensure_authenticated(request)
    check_csrf(request, csrf)
    result = tick_active_paper_deals(db)
    return {**result, "execution": "paper", "orders_sent_to_exchange": False}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "csrf_token": csrf_token(request)}
    )


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    password: str = Form(...),
    csrf: str = Form(..., alias="csrf_token"),
):
    check_csrf(request, csrf)
    if not verify_password(password, settings.admin_password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Invalid credentials or dashboard not configured.",
                "csrf_token": csrf_token(request),
            },
            status_code=401,
        )
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
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    saved: bool = False,
):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return render_dashboard(request, db, saved=saved)


@app.post("/strategies/save", response_class=HTMLResponse)
def save_strategy(
    request: Request,
    csrf: str = Form(..., alias="csrf_token"),
    strategy_id: str = Form(""),
    reference_price: str = Form(""),
    name: str = Form(...),
    symbols: str = Form("BTCUSDT"),
    pair_selection_mode: str = Form("manual"),
    discovery_min_quote_volume_24h: float = Form(5_000_000),
    discovery_max_spread_pct: float = Form(0.25),
    discovery_max_candidates: int = Form(10),
    base_order_quote: float = Form(...),
    safety_order_quote: float = Form(...),
    max_safety_orders: int = Form(...),
    take_profit_pct: float = Form(...),
    take_profit_basis: str = Form("average_entry"),
    dca_mode: str = Form("position_averaging"),
    averaging_method: str = Form("fixed_order_value"),
    safety_order_base_quantity: float = Form(0.001),
    initial_deviation_pct: float = Form(...),
    max_active_deals: int = Form(1),
    order_size_mode: str = Form("fixed_quote"),
    base_order_balance_pct: float = Form(1),
    safety_order_volume_scale: float = Form(1.5),
    deviation_scale: float = Form(1.5),
    trailing_take_profit_enabled: bool = Form(False),
    trailing_activation_pct: float = Form(1),
    trailing_callback_pct: float = Form(0.3),
    stop_loss_enabled: bool = Form(False),
    stop_loss_pct: float = Form(10),
    stop_loss_basis: str = Form("base_order"),
    move_stop_to_breakeven_enabled: bool = Form(False),
    breakeven_activation_pct: float = Form(1),
    breakeven_offset_pct: float = Form(0),
    reinvest_profit: bool = Form(False),
    max_deal_duration_hours: int = Form(0),
    start_condition: str = Form("manual"),
    auto_paper_enabled: bool = Form(False),
    entry_filter: str = Form("manual"),
    timeframe: str = Form("1h"),
    ema_fast: int = Form(50),
    ema_slow: int = Form(200),
    rsi_period: int = Form(14),
    rsi_max_entry: float = Form(40),
    adx_min: float = Form(20),
    atr_period: int = Form(14),
    cooldown_seconds: int = Form(300),
    order_timeout_seconds: int = Form(60),
    max_capital_per_deal: float = Form(100),
    max_total_allocation: float = Form(120),
    reserve_balance: float = Form(20),
    daily_loss_limit_quote: float = Form(25),
    max_equity_drawdown_pct: float = Form(5),
    min_net_profit_pct: float = Form(0.3),
    fee_rate_pct_per_side: float = Form(0.1),
    slippage_pct_per_side: float = Form(0.05),
    retry_limit: int = Form(3),
    reconciliation_seconds: int = Form(30),
    order_type: str = Form("limit"),
    db: Session = Depends(get_db),
):
    ensure_authenticated(request)
    check_csrf(request, csrf)
    try:
        if auto_paper_enabled and start_condition != "autonomous":
            raise ValueError("Autonomous paper mode requires the Autonomous start condition.")
        config = StrategyConfig(
            name=name,
            symbols=[item.strip() for item in symbols.split(",") if item.strip()] or ["BTC/USDT"],
            pair_selection_mode=pair_selection_mode,
            discovery_min_quote_volume_24h=discovery_min_quote_volume_24h,
            discovery_max_spread_pct=discovery_max_spread_pct,
            discovery_max_candidates=discovery_max_candidates,
            base_order_quote=base_order_quote,
            safety_order_quote=safety_order_quote,
            max_safety_orders=max_safety_orders,
            take_profit_pct=take_profit_pct,
            take_profit_basis=take_profit_basis,
            dca_mode=dca_mode,
            averaging_method=averaging_method,
            safety_order_base_quantity=safety_order_base_quantity,
            initial_deviation_pct=initial_deviation_pct,
            max_active_deals=max_active_deals,
            order_size_mode=order_size_mode,
            base_order_balance_pct=base_order_balance_pct,
            safety_order_volume_scale=safety_order_volume_scale,
            deviation_scale=deviation_scale,
            trailing_take_profit_enabled=trailing_take_profit_enabled,
            trailing_activation_pct=trailing_activation_pct,
            trailing_callback_pct=trailing_callback_pct,
            stop_loss_enabled=stop_loss_enabled,
            stop_loss_pct=stop_loss_pct,
            stop_loss_basis=stop_loss_basis,
            move_stop_to_breakeven_enabled=move_stop_to_breakeven_enabled,
            breakeven_activation_pct=breakeven_activation_pct,
            breakeven_offset_pct=breakeven_offset_pct,
            reinvest_profit=reinvest_profit,
            max_deal_duration_hours=max_deal_duration_hours,
            start_condition=start_condition,
            entry_filter=entry_filter,
            timeframe=timeframe,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi_period=rsi_period,
            rsi_max_entry=rsi_max_entry,
            adx_min=adx_min,
            atr_period=atr_period,
            cooldown_seconds=cooldown_seconds,
            order_timeout_seconds=order_timeout_seconds,
            max_capital_per_deal=max_capital_per_deal,
            max_total_allocation=max_total_allocation,
            reserve_balance=reserve_balance,
            daily_loss_limit_quote=daily_loss_limit_quote,
            max_equity_drawdown_pct=max_equity_drawdown_pct,
            min_net_profit_pct=min_net_profit_pct,
            fee_rate_pct_per_side=fee_rate_pct_per_side,
            slippage_pct_per_side=slippage_pct_per_side,
            retry_limit=retry_limit,
            reconciliation_seconds=reconciliation_seconds,
            order_type=order_type,
        )
        price_for_estimate = float(reference_price) if reference_price.strip() else None
        required_capital, total_capacity = estimate_profile_capital(config, price_for_estimate)
        if required_capital is not None and required_capital > config.max_capital_per_deal:
            raise ValueError(
                f"Estimated deal capital ({required_capital:.2f} USDT) exceeds "
                f"max capital per deal ({config.max_capital_per_deal:.2f} USDT)."
            )
        if config.max_capital_per_deal * config.max_active_deals > config.max_total_allocation:
            raise ValueError(
                "Max capital per deal multiplied by active deals exceeds total allocation cap."
            )
        if config.estimated_net_tp_pct() < config.min_net_profit_pct:
            raise ValueError(
                "Estimated net take profit is below the configured minimum after fee/slippage assumptions."
            )
    except Exception as exc:
        return render_dashboard(
            request, db, error=str(exc), status_code=422
        )

    record = None
    if strategy_id:
        try:
            record_id = int(strategy_id)
            record = db.get(StrategyRecord, record_id)
        except (ValueError, TypeError):
            record = None
        if record is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
    else:
        record = StrategyRecord(name=config.name, config_json=config.model_dump_json(), enabled=False)
        db.add(record)
        db.flush()

    record.name = config.name
    record.config_json = config.model_dump_json()
    # This flag enables only the paper-mode autonomous worker; exchange execution remains disabled.
    record.enabled = auto_paper_enabled
    db.add(AuditEvent(
        event_type="strategy_saved",
        detail=json.dumps({
            "id": record.id,
            "name": config.name,
            "symbols": config.symbols,
            "estimated_deal_capital": required_capital,
            "estimated_total_capacity": total_capacity,
        }),
    ))
    db.commit()
    return RedirectResponse("/?saved=true#strategies", status_code=303)
