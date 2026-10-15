from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json

from sqlalchemy.orm import Session

D = Decimal


@dataclass(frozen=True)
class CapitalEstimate:
    base_order: Decimal
    safety_orders: Decimal
    total: Decimal
    count: int


def quote_asset(symbol: str) -> str:
    """Return the quote asset from a normalized BASE/QUOTE symbol."""
    normalized = str(symbol).strip().upper().replace("-", "/")
    parts = normalized.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid trading symbol: {symbol!r}")
    return parts[1]


def estimate_capital(base_order: Decimal, safety_order: Decimal, safety_count: int, volume_scale: Decimal) -> CapitalEstimate:
    """Estimate quote capital for base + all configured safety orders."""
    if base_order <= 0 or safety_order <= 0:
        raise ValueError("Order sizes must be positive")
    if not 0 <= safety_count <= 20:
        raise ValueError("Safety order count must be between 0 and 20")
    if volume_scale < 1:
        raise ValueError("Safety order volume scale must be >= 1")
    sizes = [safety_order * (volume_scale ** i) for i in range(safety_count)]
    safety_total = sum(sizes, D("0"))
    return CapitalEstimate(base_order, safety_total, base_order + safety_total, safety_count)


def estimated_net_profit_pct(target_pct: Decimal, fee_per_side_pct: Decimal, slippage_per_side_pct: Decimal) -> Decimal:
    """A conservative, approximate round-trip estimate. Not a realized PnL calculation."""
    if min(target_pct, fee_per_side_pct, slippage_per_side_pct) < 0:
        raise ValueError("Profit, fee and slippage assumptions cannot be negative")
    return target_pct - D("2") * fee_per_side_pct - D("2") * slippage_per_side_pct


def validate_budget(required_capital: Decimal, per_deal_cap: Decimal, total_cap: Decimal, active_deals: int) -> None:
    if active_deals < 1:
        raise ValueError("At least one active deal slot must be configured")
    if required_capital > per_deal_cap:
        raise ValueError(f"Required capital {required_capital} exceeds per-deal cap {per_deal_cap}")
    if per_deal_cap * active_deals > total_cap:
        raise ValueError("Per-deal cap multiplied by active deals exceeds the total allocation cap")


def reserved_capital_by_quote(active_deals) -> dict[str, Decimal]:
    """Reserve each active deal's full configured cap, grouped by quote asset.

    USDT, BTC, USDC, etc. are separate capital buckets. They are never added
    together because a Decimal amount in one quote currency has no meaning in
    another quote currency without an explicit FX/market conversion.
    """
    totals: dict[str, Decimal] = {}
    for deal in active_deals:
        quote = quote_asset(deal.symbol)
        cap = Decimal(str(deal.config.max_capital_per_deal))
        if not cap.is_finite() or cap < 0:
            raise ValueError("Active deal has an invalid capital reservation")
        totals[quote] = totals.get(quote, D("0")) + cap
    return totals


def reserved_capital_quote(active_deals) -> Decimal:
    """Backward-compatible single-bucket helper.

    It is intentionally strict: mixed quote currencies cannot be silently
    collapsed into one number. New code should use reserved_capital_by_quote().
    """
    buckets = reserved_capital_by_quote(active_deals)
    if not buckets:
        return D("0")
    if len(buckets) != 1:
        raise ValueError("Capital is denominated in multiple quote currencies; use quote-aware buckets")
    return next(iter(buckets.values()))


def _parse_decimal_map(value: str | None) -> dict[str, Decimal]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        # Legacy single-quote state stored a plain Decimal string.
        try:
            return {"USDT": D(value)}
        except Exception as exc:
            raise ValueError("Invalid persisted risk equity state") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Persisted risk equity state must be a quote map")
    result: dict[str, Decimal] = {}
    for key, raw in parsed.items():
        quote = str(key).upper().strip()
        amount = D(str(raw))
        if not quote or not amount.is_finite() or amount < 0:
            raise ValueError("Invalid persisted quote risk state")
        result[quote] = amount
    return result


def _dump_decimal_map(values: dict[str, Decimal]) -> str:
    return json.dumps({k: str(v) for k, v in sorted(values.items())}, separators=(",", ":"), sort_keys=True)


def _single_or_none(values: dict[str, Decimal]) -> str | None:
    if len(values) == 1:
        return str(next(iter(values.values())))
    return None


def strategy_risk_status(session: Session, strategy_record, now: datetime | None = None) -> dict[str, object]:
    """Evaluate quote-aware loss guards and persist per-quote high-water marks.

    Every DCA deal is accounted in its symbol's quote asset. No BTC amount is
    ever added to a USDT amount. For each quote bucket, the configured
    max_total_allocation + reserve_balance forms the virtual paper reference
    equity. Drawdown and daily loss limits are evaluated independently per quote.
    """
    from .models import AuditEvent, PaperDealRecord, StrategyRiskState
    from .paper_engine import deserialize_paper_deal, estimate_unrealized_pnl_quote
    from .strategy import StrategyConfig
    from .config import get_settings

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    config = StrategyConfig.model_validate_json(strategy_record.config_json)
    rows = session.query(PaperDealRecord).filter_by(strategy_id=strategy_record.id).all()

    realized_by_quote: dict[str, Decimal] = {}
    realized_today_by_quote: dict[str, Decimal] = {}
    unrealized_by_quote: dict[str, Decimal] = {}
    active_quotes: set[str] = set()

    for row in rows:
        deal = deserialize_paper_deal(row.state_json)
        if row.status != deal.status:
            raise ValueError(
                f"Persisted paper deal status mismatch for {row.id}; risk evaluation must fail closed until state is reconciled"
            )
        quote = quote_asset(deal.symbol)
        if deal.status == "closed":
            realized_by_quote[quote] = realized_by_quote.get(quote, D("0")) + deal.realized_pnl_quote
            updated_at = row.updated_at
            if updated_at is not None:
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                else:
                    updated_at = updated_at.astimezone(timezone.utc)
                if updated_at.date() == current_time.date():
                    realized_today_by_quote[quote] = realized_today_by_quote.get(quote, D("0")) + deal.realized_pnl_quote
        elif deal.status == "active":
            active_quotes.add(quote)
            marked_at = row.updated_at
            if marked_at is None:
                raise ValueError(f"Active paper deal {row.id} has no mark timestamp")
            if marked_at.tzinfo is None:
                marked_at = marked_at.replace(tzinfo=timezone.utc)
            else:
                marked_at = marked_at.astimezone(timezone.utc)
            mark_age_seconds = (current_time - marked_at).total_seconds()
            max_mark_age_seconds = max(60, get_settings().paper_poll_interval_seconds * 3)
            if mark_age_seconds < -60 or mark_age_seconds > max_mark_age_seconds:
                raise ValueError(f"Active paper deal {row.id} mark is stale; risk evaluation must fail closed")
            unrealized_by_quote[quote] = unrealized_by_quote.get(quote, D("0")) + estimate_unrealized_pnl_quote(deal)

    # The configuration uses scalar capital limits, so each quote currency gets
    # its own independent virtual bucket. This is deliberately conservative and
    # avoids inventing an FX conversion that the bot has not been configured for.
    configured_quotes = {quote_asset(symbol) for symbol in config.symbols}
    all_quotes = configured_quotes | active_quotes | set(realized_by_quote) | set(unrealized_by_quote)

    state = session.get(StrategyRiskState, strategy_record.id)
    if state is None:
        reference_by_quote = {
            quote: D(str(config.max_total_allocation)) + D(str(config.reserve_balance))
            for quote in sorted(all_quotes)
        }
        peak_by_quote = dict(reference_by_quote)
        state = StrategyRiskState(
            strategy_id=strategy_record.id,
            reference_equity_quote=_dump_decimal_map(reference_by_quote),
            peak_equity_quote=_dump_decimal_map(peak_by_quote),
            drawdown_tripped=False,
        )
        session.add(state)
    else:
        reference_by_quote = _parse_decimal_map(state.reference_equity_quote)
        peak_by_quote = _parse_decimal_map(state.peak_equity_quote)
        # Legacy state has no knowledge of newly configured quote assets. Seed
        # only the missing buckets and preserve existing high-water marks.
        for quote in sorted(all_quotes):
            if quote not in reference_by_quote:
                reference_by_quote[quote] = D(str(config.max_total_allocation)) + D(str(config.reserve_balance))
            peak_by_quote.setdefault(quote, reference_by_quote[quote])

    equity_by_quote: dict[str, Decimal] = {}
    drawdown_by_quote: dict[str, Decimal] = {}
    daily_loss_by_quote: dict[str, Decimal] = {}
    tripped_quote_reasons: dict[str, str] = {}

    for quote in sorted(all_quotes):
        reference = reference_by_quote[quote]
        equity = reference + realized_by_quote.get(quote, D("0")) + unrealized_by_quote.get(quote, D("0"))
        equity_by_quote[quote] = equity
        peak = max(peak_by_quote.get(quote, reference), equity)
        peak_by_quote[quote] = peak
        drawdown = D("0") if peak <= 0 else max(D("0"), (peak - equity) / peak * D("100"))
        drawdown_by_quote[quote] = drawdown
        daily_loss = realized_today_by_quote.get(quote, D("0"))
        daily_loss_by_quote[quote] = daily_loss

        if drawdown >= D(str(config.max_equity_drawdown_pct)):
            tripped_quote_reasons[quote] = (
                f"{quote} equity drawdown {drawdown:.4f}% reached limit "
                f"{config.max_equity_drawdown_pct:.4f}%"
            )
        elif daily_loss <= -D(str(config.daily_loss_limit_quote)):
            tripped_quote_reasons[quote] = (
                f"{quote} daily realized loss {daily_loss:.8f} reached limit "
                f"-{config.daily_loss_limit_quote:.8f}"
            )

    if tripped_quote_reasons and not state.drawdown_tripped:
        state.drawdown_tripped = True
        first_quote, first_reason = next(iter(tripped_quote_reasons.items()))
        state.drawdown_trip_reason = f"{first_quote}: {first_reason}"
        session.add(AuditEvent(
            event_type="risk_drawdown_tripped",
            detail=json.dumps({
                "strategy_id": strategy_record.id,
                "reasons_by_quote": tripped_quote_reasons,
                "drawdown_pct_by_quote": {k: str(v) for k, v in drawdown_by_quote.items()},
                "equity_by_quote": {k: str(v) for k, v in equity_by_quote.items()},
                "peak_equity_by_quote": {k: str(v) for k, v in peak_by_quote.items()},
            }),
        ))

    state.reference_equity_quote = _dump_decimal_map(reference_by_quote)
    state.peak_equity_quote = _dump_decimal_map(peak_by_quote)
    session.commit()

    reason = state.drawdown_trip_reason if state.drawdown_tripped else None
    if reason is None and tripped_quote_reasons:
        reason = next(iter(tripped_quote_reasons.values()))

    return {
        "blocked": bool(reason),
        "reason": reason,
        "reference_equity_quote": _single_or_none(reference_by_quote),
        "equity_quote": _single_or_none(equity_by_quote),
        "peak_equity_quote": _single_or_none(peak_by_quote),
        "drawdown_pct": _single_or_none(drawdown_by_quote),
        "drawdown_tripped": bool(state.drawdown_tripped),
        "daily_realized_pnl_quote": _single_or_none(daily_loss_by_quote),
        "daily_loss_tripped": bool(tripped_quote_reasons),
        "reference_equity_by_quote": {k: str(v) for k, v in sorted(reference_by_quote.items())},
        "equity_by_quote": {k: str(v) for k, v in sorted(equity_by_quote.items())},
        "peak_equity_by_quote": {k: str(v) for k, v in sorted(peak_by_quote.items())},
        "drawdown_pct_by_quote": {k: str(v) for k, v in sorted(drawdown_by_quote.items())},
        "daily_realized_pnl_by_quote": {k: str(v) for k, v in sorted(daily_loss_by_quote.items())},
        "risk_reasons_by_quote": tripped_quote_reasons,
    }
