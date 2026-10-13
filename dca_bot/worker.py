from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from decimal import Decimal as D
from typing import Any

from sqlalchemy.orm import Session

from .db import SessionLocal
from .config import get_settings
from .market_data import BinanceSpotMarketData
from .autonomous import analyze_market
from .models import (
    AuditEvent,
    AutonomousHistoryState,
    AutonomousScanCheckpoint,
    PaperDealRecord,
    StrategyRecord,
)
from .strategy import StrategyConfig
from .paper_engine import deserialize_paper_deal, serialize_paper_deal, start_paper_deal, update_paper_deal
from .risk import global_capital_status, reserved_capital_quote, strategy_risk_status
from .entry_guard import serialized_entry

logger = logging.getLogger(__name__)


@serialized_entry
def tick_active_paper_deals(db: Session | None = None) -> dict[str, list[dict[str, Any]]]:
    """Poll each active symbol once and persist the deterministic paper state transitions."""
    owns_session = db is None
    session = db or SessionLocal()
    updated: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    try:
        rows = session.query(PaperDealRecord).filter_by(status="active").all()
        if not rows:
            return {"updated": updated, "failures": failures}
        # SQLite's legacy transaction mode may not begin a physical transaction
        # for SELECTs. Explicit BEGIN ensures released savepoints remain covered
        # by the outer transaction and can still be rolled back if final commit fails.
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")
        exchange = BinanceSpotMarketData()
        prices = {}
        for row in rows:
            savepoint = session.begin_nested()
            try:
                deal = deserialize_paper_deal(row.state_json)
                if row.id != deal.id or row.status != deal.status or row.symbol != deal.symbol:
                    raise ValueError(
                        f"Persisted paper deal status mismatch, symbol mismatch, or ID mismatch for {row.id}; "
                        "manual reconciliation is required"
                    )
                if deal.symbol not in prices:
                    prices[deal.symbol] = exchange.ticker(deal.symbol).last
                events = update_paper_deal(deal, prices[deal.symbol])
                row.status = deal.status
                row.state_json = serialize_paper_deal(deal)
                event_types = [event.event_type for event in events]
                updated_item = {"deal": deal.snapshot(), "events": event_types}
                if events:
                    session.add(AuditEvent(
                        event_type="paper_deal_updated",
                        detail=json.dumps({
                            "deal_id": deal.id,
                            "symbol": deal.symbol,
                            "events": event_types,
                            "status": deal.status,
                        }),
                    ))
                if (
                    deal.status == "closed"
                    and deal.config.reinvest_profit
                    and deal.realized_pnl_quote > 0
                    and "deal_closed" in event_types
                ):
                    strategy_record = session.get(StrategyRecord, row.strategy_id)
                    if strategy_record is not None:
                        try:
                            prior = StrategyConfig.model_validate_json(strategy_record.config_json)
                            candidate = StrategyConfig.model_validate({
                                **prior.model_dump(),
                                "base_order_quote": prior.base_order_quote + float(deal.realized_pnl_quote),
                            })
                            required = candidate.estimated_max_deal_capital(float(deal.last_price))
                            if required <= candidate.max_capital_per_deal and required * candidate.max_active_deals <= candidate.max_total_allocation:
                                strategy_record.config_json = candidate.model_dump_json()
                                strategy_record.name = candidate.name
                                session.add(AuditEvent(
                                    event_type="paper_profit_reinvested",
                                    detail=json.dumps({
                                        "deal_id": deal.id,
                                        "strategy_id": row.strategy_id,
                                        "profit_quote": str(deal.realized_pnl_quote),
                                        "next_base_order_quote": candidate.base_order_quote,
                                    }),
                                ))
                            else:
                                session.add(AuditEvent(
                                    event_type="paper_reinvestment_blocked",
                                    detail=json.dumps({"deal_id": deal.id, "reason": "capital cap would be exceeded"}),
                                ))
                        except ValueError:
                            session.add(AuditEvent(
                                event_type="paper_reinvestment_blocked",
                                detail=json.dumps({"deal_id": deal.id, "reason": "updated strategy failed validation"}),
                            ))
                savepoint.commit()
                updated.append(updated_item)
            except Exception as exc:
                if savepoint.is_active:
                    savepoint.rollback()
                logger.warning("Paper deal poll failed for %s: %s", row.id, exc)
                failures.append({
                    "deal_id": row.id,
                    "symbol": row.symbol,
                    "error": "Market update or state processing failed",
                })
        for strategy_record in session.query(StrategyRecord).all():
            try:
                strategy_risk_status(session, strategy_record)
            except Exception as exc:
                logger.warning("Paper risk evaluation failed for strategy %s: %s", strategy_record.id, exc)
                failures.append({
                    "strategy_id": str(strategy_record.id),
                    "symbol": "*",
                    "error": "Risk evaluation failed; new entries must remain blocked",
                })
        session.commit()
        return {"updated": updated, "failures": failures}
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


@serialized_entry
def _fresh_closed_candles(exchange: BinanceSpotMarketData, symbol: str, config: StrategyConfig, now: float) -> tuple[list[list[float]], int]:
    """Load enough Spot OHLCV and validate the latest closed candle before analysis."""
    limit = min(1000, max(config.ema_slow + 50, config.atr_period * 3 + 10, 300))
    candles = exchange.candles(symbol, config.timeframe, limit=limit)
    if len(candles) < 2:
        raise ValueError("Not enough closed Spot candles")
    closed_candles = candles[:-1]  # Never decide on the still-forming candle.
    candle_timestamp_ms = int(closed_candles[-1][0])
    timeframe_seconds = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900,
        "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
        "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
    }.get(config.timeframe)
    if timeframe_seconds is None:
        raise ValueError("Unsupported timeframe for candle freshness validation")
    candle_age_seconds = now - (candle_timestamp_ms / 1000)
    if candle_age_seconds < -60 or candle_age_seconds > timeframe_seconds * 2:
        raise ValueError("Latest closed candle is stale or has a future timestamp")
    return closed_candles, candle_timestamp_ms


def _read_autonomous_history_from_audit(
    session: Session,
) -> tuple[dict[tuple[int, str], dict[str, Any]], dict[tuple[int, str], float]]:
    """One-time legacy bootstrap from append-only audit events."""
    events = (
        session.query(AuditEvent)
        .filter(AuditEvent.event_type.in_(
            ("autonomous_decision", "autonomous_entry_started")
        ))
        .order_by(AuditEvent.id.desc())
        .all()
    )
    last_decision: dict[tuple[int, str], dict[str, Any]] = {}
    last_entry: dict[tuple[int, str], float] = {}
    for event in events:
        try:
            detail = json.loads(event.detail)
            key = (int(detail["strategy_id"]), str(detail["symbol"]))
            if event.event_type == "autonomous_decision":
                last_decision.setdefault(key, detail)
            elif event.event_type == "autonomous_entry_started":
                last_entry.setdefault(key, float(detail["started_at_epoch"]))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return last_decision, last_entry


def _load_autonomous_history(
    session: Session,
) -> tuple[dict[tuple[int, str], dict[str, Any]], dict[tuple[int, str], float]]:
    """Load compact checkpoints; import legacy audit history only on first startup."""
    state = session.get(AutonomousHistoryState, 1)
    if state is None:
        last_decision, last_entry = _read_autonomous_history_from_audit(session)
        for key in set(last_decision) | set(last_entry):
            session.add(AutonomousScanCheckpoint(
                strategy_id=key[0],
                symbol=key[1],
                last_decision_json=(
                    json.dumps(last_decision[key]) if key in last_decision else None
                ),
                last_entry_epoch=last_entry.get(key),
            ))
        session.add(AutonomousHistoryState(id=1, initialized=True))
        session.flush()
        return last_decision, last_entry

    last_decision: dict[tuple[int, str], dict[str, Any]] = {}
    last_entry: dict[tuple[int, str], float] = {}
    for checkpoint in session.query(AutonomousScanCheckpoint).all():
        key = (checkpoint.strategy_id, checkpoint.symbol)
        if checkpoint.last_decision_json:
            try:
                detail = json.loads(checkpoint.last_decision_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Corrupt autonomous scanner decision checkpoint for {key[0]} / {key[1]}; "
                    "entries blocked until checkpoint recovery"
                ) from exc
            if not isinstance(detail, dict):
                raise ValueError(
                    f"Corrupt autonomous scanner decision checkpoint for {key[0]} / {key[1]}; "
                    "entries blocked until checkpoint recovery"
                )
            try:
                detail_key = (int(detail["strategy_id"]), str(detail["symbol"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Corrupt autonomous scanner decision checkpoint for {key[0]} / {key[1]}; "
                    "entries blocked until checkpoint recovery"
                ) from exc
            if detail_key != key:
                raise ValueError(
                    f"Autonomous scanner checkpoint identity mismatch for {key[0]} / {key[1]}; "
                    "entries blocked until checkpoint recovery"
                )
            last_decision[key] = detail
        if checkpoint.last_entry_epoch is not None:
            entry_epoch = float(checkpoint.last_entry_epoch)
            if not math.isfinite(entry_epoch) or entry_epoch < 0:
                raise ValueError(
                    f"Corrupt autonomous scanner entry checkpoint for {key[0]} / {key[1]}; "
                    "entries blocked until checkpoint recovery"
                )
            last_entry[key] = entry_epoch
    return last_decision, last_entry


def _checkpoint_for_pair(
    session: Session, key: tuple[int, str]
) -> AutonomousScanCheckpoint:
    checkpoint = session.get(AutonomousScanCheckpoint, key)
    if checkpoint is None:
        checkpoint = AutonomousScanCheckpoint(strategy_id=key[0], symbol=key[1])
        session.add(checkpoint)
    return checkpoint

@serialized_entry
def scan_autonomous_strategies(db: Session | None = None) -> dict[str, Any]:
    """Evaluate enabled autonomous profiles and open paper deals only on a fresh closed candle."""
    owns_session = db is None
    session = db or SessionLocal()
    result: dict[str, Any] = {"started": [], "waited": [], "failures": []}
    try:
        enabled_profiles = session.query(StrategyRecord).filter_by(enabled=True).all()
        profiles = []
        for row in enabled_profiles:
            try:
                profile_config = StrategyConfig.model_validate_json(row.config_json)
            except Exception as exc:
                # One corrupted or obsolete profile must not abort scans for every
                # other strategy. Fail closed for this profile and keep scanning.
                logger.warning("Invalid strategy configuration for profile %s: %s", row.id, exc)
                result["failures"].append({
                    "strategy_id": row.id,
                    "error": "Invalid strategy configuration; entries blocked",
                })
                continue
            if profile_config.start_condition == "autonomous":
                profiles.append(row)
        if not profiles:
            return result

        # SQLite's legacy transaction mode may not begin a physical transaction for SELECTs.
        # Begin before the one-time checkpoint bootstrap so marker, checkpoints, deals,
        # and audit events all share the final commit/rollback boundary.
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")

        last_decision, last_entry = _load_autonomous_history(session)
        exchange = BinanceSpotMarketData()
        now = time.time()
        for profile in profiles:
            try:
                config = StrategyConfig.model_validate_json(profile.config_json)
                risk_status = strategy_risk_status(session, profile)
            except Exception as exc:
                result["failures"].append({"strategy_id": profile.id, "error": "Risk evaluation failed; entries blocked"})
                logger.warning("Risk evaluation failed for strategy %s: %s", profile.id, exc)
                continue
            if risk_status["blocked"]:
                session.add(AuditEvent(
                    event_type="risk_entry_blocked",
                    detail=json.dumps({"strategy_id": profile.id, "reason": risk_status["reason"], "source": "autonomous"}),
                ))
                for symbol in config.symbols:
                    result["waited"].append({"strategy_id": profile.id, "symbol": symbol, "reason": str(risk_status["reason"])})
                continue

            if config.pair_selection_mode == "automatic_usdt":
                try:
                    symbols_to_scan = exchange.discover_usdt_symbols(
                        min_quote_volume_24h=config.discovery_min_quote_volume_24h,
                        max_spread_pct=config.discovery_max_spread_pct,
                        limit=config.discovery_max_candidates,
                    )
                except Exception as exc:
                    logger.warning("Automatic USDT pair discovery failed for strategy %s: %s", profile.id, exc)
                    result["failures"].append({
                        "strategy_id": profile.id,
                        "error": "Automatic USDT pair discovery unavailable; entries blocked",
                    })
                    continue
                if not symbols_to_scan:
                    result["waited"].append({
                        "strategy_id": profile.id,
                        "symbol": "*",
                        "reason": "No active USDT Spot pair passed liquidity, spread, and ticker-freshness filters",
                    })
                    continue
            else:
                symbols_to_scan = config.symbols

            # Analyze the bounded automatic candidate set once, then evaluate the
            # strongest strategy signals first instead of opening deals by volume rank.
            prefetched: dict[str, tuple[list[list[float]], int, Any]] = {}
            prefetch_failures: dict[str, str] = {}
            if config.pair_selection_mode == "automatic_usdt":
                for candidate_symbol in symbols_to_scan:
                    try:
                        closed, candle_timestamp = _fresh_closed_candles(
                            exchange, candidate_symbol, config, now
                        )
                        candidate_decision = analyze_market(
                            candidate_symbol, config.timeframe, closed, config
                        )
                        prefetched[candidate_symbol] = (
                            closed, candle_timestamp, candidate_decision
                        )
                    except Exception as exc:
                        prefetch_failures[candidate_symbol] = str(exc)
                symbols_to_scan = sorted(
                    symbols_to_scan,
                    key=lambda candidate_symbol: (
                        candidate_symbol in prefetched
                        and prefetched[candidate_symbol][2].action == "candidate",
                        prefetched[candidate_symbol][2].score
                        if candidate_symbol in prefetched else -1,
                    ),
                    reverse=True,
                )

            for symbol in symbols_to_scan:
                key = (profile.id, symbol)
                started_count = len(result["started"])
                waited_count = len(result["waited"])
                previous_entry = last_entry.get(key)
                had_previous_entry = key in last_entry
                previous_decision = last_decision.get(key)
                had_previous_decision = key in last_decision
                try:
                    with session.begin_nested():
                        active_rows = session.query(PaperDealRecord).filter_by(status="active").all()
                        active_deals = [
                            (row, deserialize_paper_deal(row.state_json)) for row in active_rows
                        ]
                        strategy_deals = [item for item in active_deals if item[0].strategy_id == profile.id]
                        symbol_deals = [item for item in strategy_deals if item[1].symbol == symbol]
                        if config.pair_selection_mode == "automatic_usdt" and any(
                            deal.symbol == symbol for _, deal in active_deals
                        ):
                            result["waited"].append({
                                "strategy_id": profile.id,
                                "symbol": symbol,
                                "reason": "pair already has an active deal in another strategy",
                            })
                            continue
                        if len(strategy_deals) >= config.max_active_deals:
                            result["waited"].append({"strategy_id": profile.id, "symbol": symbol, "reason": "active deal limit"})
                            continue
                        if len(symbol_deals) >= config.max_active_deals_per_symbol:
                            result["waited"].append({"strategy_id": profile.id, "symbol": symbol, "reason": "pair already has an active deal"})
                            continue

                        if config.pair_selection_mode == "automatic_usdt":
                            if symbol in prefetch_failures:
                                raise ValueError("Automatic candidate analysis failed freshness or market-data checks")
                            closed_candles, candle_timestamp_ms, decision = prefetched[symbol]
                        else:
                            closed_candles, candle_timestamp_ms = _fresh_closed_candles(
                                exchange, symbol, config, now
                            )
                            decision = None
                        prior = last_decision.get(key, {})
                        if prior.get("candle_timestamp_ms") == candle_timestamp_ms:
                            continue

                        if decision is None:
                            decision = analyze_market(symbol, config.timeframe, closed_candles, config)
                        action = decision.action
                        reason = decision.reason
                        params = decision.parameters
                        if action == "candidate":
                            last_started = last_entry.get(key, 0.0)
                            if now - last_started < config.cooldown_seconds:
                                action, reason = "wait", "Configured cooldown has not elapsed"
                            else:
                                required = float(params.get("estimated_deal_capital_quote", float("inf")))
                                used_capacity = reserved_capital_quote(
                                    [deal for _, deal in strategy_deals]
                                )
                                new_reservation = D(str(config.max_capital_per_deal))
                                total_cap = D(str(config.max_total_allocation))
                                # Reuse the account-wide guard, which also validates every
                                # active persisted row/state pair. A mismatch in another
                                # strategy must block this entry rather than be treated as
                                # a valid global reservation snapshot.
                                global_status = global_capital_status(
                                    session, D(str(get_settings().global_capital_limit_quote))
                                )
                                global_limit = D(global_status["limit_quote"])
                                global_reserved = D(global_status["reserved_quote"])
                                if global_reserved + new_reservation > global_limit:
                                    action, reason = "wait", "Global capital limit would be exceeded by active and new deal reservations across all strategies"
                                elif used_capacity + new_reservation > total_cap:
                                    action, reason = "wait", "Total allocation cap would be exceeded by active and new deal reservations"
                                elif not params.get("capital_safe", False):
                                    action, reason = "wait", "Adaptive DCA ladder exceeds the capital cap"
                                else:
                                    adaptive_config = config.model_copy(update={
                                        "symbols": [symbol],
                                        "start_condition": "immediately",
                                        "entry_filter": "none",
                                        "initial_deviation_pct": float(params["initial_deviation_pct"]),
                                        "deviation_scale": float(params["deviation_scale"]),
                                        "take_profit_pct": float(params["take_profit_pct"]),
                                        "max_safety_orders": int(params["max_safety_orders"]),
                                        "safety_order_volume_scale": float(params["safety_order_volume_scale"]),
                                    })
                                    price = float(closed_candles[-1][4])
                                    # Minimum-order metadata is a required preflight. Missing or failing validation blocks entry.
                                    exchange.validate_minimum_order_sizes(symbol, adaptive_config, price)
                                    deal = start_paper_deal(adaptive_config, symbol, price)
                                    session.add(PaperDealRecord(
                                        id=deal.id,
                                        strategy_id=profile.id,
                                        symbol=deal.symbol,
                                        status=deal.status,
                                        state_json=serialize_paper_deal(deal),
                                    ))
                                    started_at = time.time()
                                    last_entry[key] = started_at
                                    result["started"].append({
                                        "strategy_id": profile.id,
                                        "symbol": symbol,
                                        "deal": deal.snapshot(),
                                    })
                                    session.add(AuditEvent(
                                        event_type="autonomous_entry_started",
                                        detail=json.dumps({
                                            "strategy_id": profile.id,
                                            "symbol": symbol,
                                            "deal_id": deal.id,
                                            "started_at_epoch": started_at,
                                            "candle_timestamp_ms": candle_timestamp_ms,
                                            "parameters": params,
                                        }),
                                    ))
                                    checkpoint = _checkpoint_for_pair(session, key)
                                    checkpoint.last_entry_epoch = started_at
                        detail = {
                            "strategy_id": profile.id,
                            "symbol": symbol,
                            "timeframe": config.timeframe,
                            "candle_timestamp_ms": candle_timestamp_ms,
                            "evaluated_at_epoch": now,
                            "action": action,
                            "reason": reason,
                            "score": decision.score,
                            "indicators": {
                                "close": decision.indicators.close,
                                "ema_fast": decision.indicators.ema_fast,
                                "ema_slow": decision.indicators.ema_slow,
                                "rsi": decision.indicators.rsi,
                                "atr_pct": decision.indicators.atr_pct,
                                "adx": decision.indicators.adx,
                            },
                            "parameters": params,
                        }
                        session.add(AuditEvent(
                            event_type="autonomous_decision",
                            detail=json.dumps(detail),
                        ))
                        checkpoint = _checkpoint_for_pair(session, key)
                        checkpoint.last_decision_json = json.dumps(detail)
                        last_decision[key] = detail
                        if action != "candidate":
                            result["waited"].append({
                                "strategy_id": profile.id,
                                "symbol": symbol,
                                "reason": reason,
                                "score": decision.score,
                            })

                except Exception as exc:
                    del result["started"][started_count:]
                    del result["waited"][waited_count:]
                    if had_previous_entry:
                        last_entry[key] = previous_entry
                    else:
                        last_entry.pop(key, None)
                    if had_previous_decision:
                        last_decision[key] = previous_decision
                    else:
                        last_decision.pop(key, None)
                    logger.warning("Autonomous paper scan failed for strategy %s / %s: %s", profile.id, symbol, exc)
                    result["failures"].append({
                        "strategy_id": profile.id,
                        "symbol": symbol,
                        "error": "Market analysis or paper entry failed; changes for this pair were rolled back",
                    })

        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


async def run_paper_worker(stop_event: asyncio.Event, interval_seconds: int) -> None:
    """Single-process paper-only loop. It never submits exchange orders."""
    last_autonomous_scan = 0.0
    while not stop_event.is_set():
        maintenance_safe = True
        try:
            tick_result = await asyncio.to_thread(tick_active_paper_deals)
            if tick_result.get("failures"):
                maintenance_safe = False
                logger.warning(
                    "Skipping autonomous scan because paper deal maintenance reported failures"
                )
        except Exception:
            maintenance_safe = False
            logger.exception("Paper DCA worker iteration failed; autonomous scan skipped")
        now = time.monotonic()
        if maintenance_safe and now - last_autonomous_scan >= max(60, interval_seconds):
            try:
                await asyncio.to_thread(scan_autonomous_strategies)
            except Exception:
                logger.exception("Autonomous paper strategy scan failed")
            last_autonomous_scan = time.monotonic()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass
