from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CapitalEstimate:
    base_order: Decimal
    safety_orders: Decimal
    total: Decimal
    count: int


def estimate_capital(base_order: Decimal, safety_order: Decimal, safety_count: int, volume_scale: Decimal) -> CapitalEstimate:
    """Estimate quote capital for base + all configured safety orders."""
    if base_order <= 0 or safety_order <= 0:
        raise ValueError('Order sizes must be positive')
    if not 0 <= safety_count <= 20:
        raise ValueError('Safety order count must be between 0 and 20')
    if volume_scale < 1:
        raise ValueError('Safety order volume scale must be >= 1')
    sizes = [safety_order * (volume_scale ** i) for i in range(safety_count)]
    safety_total = sum(sizes, Decimal('0'))
    return CapitalEstimate(base_order, safety_total, base_order + safety_total, safety_count)


def estimated_net_profit_pct(target_pct: Decimal, fee_per_side_pct: Decimal, slippage_per_side_pct: Decimal) -> Decimal:
    """A conservative, approximate round-trip estimate. Not a realized PnL calculation."""
    if min(target_pct, fee_per_side_pct, slippage_per_side_pct) < 0:
        raise ValueError('Profit, fee and slippage assumptions cannot be negative')
    return target_pct - Decimal('2') * fee_per_side_pct - Decimal('2') * slippage_per_side_pct


def validate_budget(required_capital: Decimal, per_deal_cap: Decimal, total_cap: Decimal, active_deals: int) -> None:
    if active_deals < 1:
        raise ValueError('At least one active deal slot must be configured')
    if required_capital > per_deal_cap:
        raise ValueError(f'Required capital {required_capital} exceeds per-deal cap {per_deal_cap}')
    if per_deal_cap * active_deals > total_cap:
        raise ValueError('Per-deal cap multiplied by active deals exceeds the total allocation cap')


def reserved_capital_quote(active_deals) -> Decimal:
    """Conservatively reserve each active deal's full configured capital cap.

    Reserving the full cap prevents manual and autonomous entry paths from
    spending the same strategy allocation twice while safety orders remain open.
    """
    total = Decimal("0")
    for deal in active_deals:
        cap = Decimal(str(deal.config.max_capital_per_deal))
        if not cap.is_finite() or cap < 0:
            raise ValueError("Active deal has an invalid capital reservation")
        total += cap
    return total


from datetime import datetime, timezone
import json
from sqlalchemy.orm import Session


def strategy_risk_status(session: Session, strategy_record, now: datetime | None = None) -> dict[str, object]:
    """Evaluate paper-only loss guards and persist a drawdown high-water mark.

    Virtual starting equity is the strategy's total allocation plus its reserve.
    Realized closed PnL and fee/slippage-aware active mark-to-market PnL are added
    to that baseline. This is a paper-account proxy, not exchange wallet equity.
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
    realized_total = Decimal("0")
    realized_today = Decimal("0")
    unrealized_active = Decimal("0")
    for row in rows:
        deal = deserialize_paper_deal(row.state_json)
        if row.status != deal.status:
            raise ValueError(
                f"Persisted paper deal status mismatch for {row.id}; "
                "risk evaluation must fail closed until state is reconciled"
            )
        if deal.status == "closed":
            realized_total += deal.realized_pnl_quote
            updated_at = row.updated_at
            if updated_at is not None:
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                else:
                    updated_at = updated_at.astimezone(timezone.utc)
                if updated_at.date() == current_time.date():
                    realized_today += deal.realized_pnl_quote
        elif deal.status == "active":
            # Never let a stale persisted mark drive drawdown decisions. The
            # worker refreshes active deals on its poll cadence; if that refresh
            # has failed or stopped, entry paths must fail closed until a fresh
            # mark is persisted.
            marked_at = row.updated_at
            if marked_at is None:
                raise ValueError(f"Active paper deal {row.id} has no mark timestamp")
            if marked_at.tzinfo is None:
                marked_at = marked_at.replace(tzinfo=timezone.utc)
            else:
                marked_at = marked_at.astimezone(timezone.utc)
            mark_age_seconds = (current_time - marked_at).total_seconds()
            max_mark_age_seconds = max(
                60,
                get_settings().paper_poll_interval_seconds * 3,
            )
            if mark_age_seconds < -60 or mark_age_seconds > max_mark_age_seconds:
                raise ValueError(
                    f"Active paper deal {row.id} mark is stale; risk evaluation must fail closed"
                )
            unrealized_active += estimate_unrealized_pnl_quote(deal)

    state = session.get(StrategyRiskState, strategy_record.id)
    if state is None:
        reference = Decimal(str(config.max_total_allocation)) + Decimal(str(config.reserve_balance))
        equity = reference + realized_total + unrealized_active
        state = StrategyRiskState(
            strategy_id=strategy_record.id,
            reference_equity_quote=str(reference),
            peak_equity_quote=str(max(reference, equity)),
            drawdown_tripped=False,
        )
        session.add(state)
    else:
        reference = Decimal(state.reference_equity_quote)
        equity = reference + realized_total + unrealized_active
        peak = Decimal(state.peak_equity_quote)
        if equity > peak:
            state.peak_equity_quote = str(equity)

    peak_equity = Decimal(state.peak_equity_quote)
    drawdown_pct = Decimal("0") if peak_equity <= 0 else max(
        Decimal("0"), (peak_equity - equity) / peak_equity * Decimal("100")
    )
    if not state.drawdown_tripped and drawdown_pct >= Decimal(str(config.max_equity_drawdown_pct)):
        state.drawdown_tripped = True
        state.drawdown_trip_reason = (
            f"Paper equity drawdown {drawdown_pct:.4f}% reached limit "
            f"{config.max_equity_drawdown_pct:.4f}%"
        )
        session.add(AuditEvent(
            event_type="risk_drawdown_tripped",
            detail=json.dumps({
                "strategy_id": strategy_record.id,
                "drawdown_pct": str(drawdown_pct),
                "limit_pct": config.max_equity_drawdown_pct,
                "equity_quote": str(equity),
                "peak_equity_quote": str(peak_equity),
            }),
        ))

    daily_loss_tripped = realized_today <= -Decimal(str(config.daily_loss_limit_quote))
    reason = state.drawdown_trip_reason if state.drawdown_tripped else None
    if state.drawdown_tripped and not reason:
        reason = "Drawdown breaker is latched but its reason is missing; manual reconciliation required"
    if reason is None and daily_loss_tripped:
        reason = (
            f"Daily realized loss {realized_today:.8f} USDT reached limit "
            f"-{config.daily_loss_limit_quote:.8f} USDT"
        )
    return {
        "blocked": reason is not None,
        "reason": reason,
        "reference_equity_quote": str(reference),
        "equity_quote": str(equity),
        "peak_equity_quote": str(peak_equity),
        "drawdown_pct": str(drawdown_pct),
        "drawdown_tripped": bool(state.drawdown_tripped),
        "daily_realized_pnl_quote": str(realized_today),
        "daily_loss_tripped": daily_loss_tripped,
    }


def global_capital_status(session: Session, limit_quote: Decimal) -> dict[str, str]:
    """Report account-wide reserved capital across all active paper deals.

    Each active deal reserves its strategy's full max_capital_per_deal, not just
    quote already spent, so future safety orders cannot overcommit the shared cap.
    """
    from .models import PaperDealRecord
    from .paper_engine import deserialize_paper_deal

    if not limit_quote.is_finite() or limit_quote <= 0:
        raise ValueError("Global capital limit must be a positive finite amount")
    rows = session.query(PaperDealRecord).filter_by(status="active").all()
    deals = []
    for row in rows:
        deal = deserialize_paper_deal(row.state_json)
        if row.status != deal.status or deal.status != "active":
            raise ValueError("Persisted active deal state is inconsistent; global capital check failed closed")
        deals.append(deal)
    reserved = reserved_capital_quote(deals)
    remaining = limit_quote - reserved
    return {
        "limit_quote": format(limit_quote.normalize(), "f"),
        "reserved_quote": format(reserved.normalize(), "f"),
        "remaining_quote": format(remaining.normalize(), "f"),
        "within_limit": str(reserved <= limit_quote).lower(),
    }
