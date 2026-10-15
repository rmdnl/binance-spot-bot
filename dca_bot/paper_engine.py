from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
import time
from typing import Literal
from uuid import uuid4

from .dca_math import SafetyOrderLevel, build_dca_summary
from .strategy import StrategyConfig

D = Decimal

@dataclass(frozen=True)
class PaperEvent:
    event_type: str
    detail: str
    price: Decimal
    quote_amount: Decimal = D("0")
    base_quantity: Decimal = D("0")

@dataclass
class PaperDeal:
    id: str
    strategy_name: str
    symbol: str
    status: Literal["active", "closed"]
    config: StrategyConfig
    base_price: Decimal
    last_price: Decimal
    quote_spent: Decimal
    base_quantity: Decimal
    entry_fees_quote: Decimal
    safety_levels: tuple[SafetyOrderLevel, ...]
    started_at_epoch: float
    safety_orders_filled: int = 0
    highest_price: Decimal = D("0")
    trailing_active: bool = False
    breakeven_active: bool = False
    exit_price: Decimal | None = None
    realized_pnl_quote: Decimal = D("0")
    close_reason: str | None = None
    last_dca_at_epoch: float | None = None
    events: list[PaperEvent] = field(default_factory=list)

    @property
    def average_entry_price(self) -> Decimal:
        if self.base_quantity <= 0:
            return D("0")
        return self.quote_spent / self.base_quantity

    @property
    def remaining_quote_capacity(self) -> Decimal:
        return max(D("0"), D(str(self.config.max_capital_per_deal)) - self.quote_spent)

    def snapshot(self) -> dict:
        return {
            "id": self.id, "strategy_name": self.strategy_name, "symbol": self.symbol,
            "status": self.status, "started_at_epoch": self.started_at_epoch,
            "base_price": str(self.base_price), "last_price": str(self.last_price),
            "quote_spent": str(self.quote_spent), "base_quantity": str(self.base_quantity),
            "average_entry_price": str(self.average_entry_price),
            "safety_orders_filled": self.safety_orders_filled,
            "safety_orders_total": len(self.safety_levels),
            "trailing_active": self.trailing_active, "breakeven_active": self.breakeven_active,
            "exit_price": str(self.exit_price) if self.exit_price is not None else None,
            "realized_pnl_quote": str(self.realized_pnl_quote), "close_reason": self.close_reason,
        }

def _positive_price(value: Decimal | str | float) -> Decimal:
    price = D(str(value))
    if not price.is_finite() or price <= 0:
        raise ValueError("Price must be a positive finite number")
    return price

def start_paper_deal(config: StrategyConfig, symbol: str, price: Decimal | str | float) -> PaperDeal:
    """Create a simulated Spot long deal and immediately simulate its base-order fill."""
    normalized = symbol.strip().upper().replace("-", "/")
    if "/" not in normalized and normalized.endswith("USDT"):
        normalized = f"{normalized[:-4]}/USDT"
    if normalized not in config.symbols:
        raise ValueError(f"{normalized} is not included in the configured pair list")
    price0 = _positive_price(price)
    if config.price_range_enabled and not (D(str(config.price_range_min)) <= price0 <= D(str(config.price_range_max))):
        raise ValueError("Current market price is outside the configured Price Range")
    if config.estimated_net_tp_pct() < config.min_net_profit_pct:
        raise ValueError("Estimated net take-profit is below min_net_profit_pct after fees and slippage; increase take_profit_pct or reduce estimated costs")
    summary = build_dca_summary(
        base_price=price0,
        base_order_quote=D(str(config.base_order_quote)),
        safety_order_quote=D(str(config.safety_order_quote)),
        safety_order_count=config.max_safety_orders,
        initial_deviation_pct=D(str(config.initial_deviation_pct)),
        deviation_scale=D(str(config.deviation_scale)),
        safety_volume_scale=D(str(config.safety_order_volume_scale)),
        averaging_method=config.averaging_method,
        safety_order_base_quantity=D(str(config.safety_order_base_quantity)),
        dca_mode=config.dca_mode,
    )
    if summary.total_quote_required > D(str(config.max_capital_per_deal)):
        raise ValueError("Estimated DCA capital exceeds max capital per deal")
    if summary.total_quote_required > D(str(config.max_total_allocation)):
        raise ValueError("Estimated DCA capital exceeds total allocation cap")
    base_qty = D(str(config.base_order_quote)) / price0
    entry_fee = D(str(config.base_order_quote)) * D(str(config.fee_rate_pct_per_side)) / D("100")
    now = time.time()
    deal = PaperDeal(
        id=str(uuid4()), strategy_name=config.name, symbol=normalized, status="active", config=config,
        base_price=price0, last_price=price0, quote_spent=D(str(config.base_order_quote)),
        base_quantity=base_qty, entry_fees_quote=entry_fee, safety_levels=summary.safety_orders,
        started_at_epoch=now, highest_price=price0,
    )
    deal.events.append(PaperEvent("base_order_filled", "Simulated base buy filled at reference price", price0, D(str(config.base_order_quote)), base_qty))
    return deal

def _close(deal: PaperDeal, price: Decimal, reason: str) -> PaperEvent:
    deal.realized_pnl_quote = estimate_unrealized_pnl_quote(deal, price)
    deal.exit_price = price
    deal.close_reason = reason
    deal.status = "closed"
    event = PaperEvent("deal_closed", reason, price, deal.base_quantity * price, deal.base_quantity)
    deal.events.append(event)
    return event

def estimate_unrealized_pnl_quote(deal: PaperDeal, price: Decimal | str | float | None = None) -> Decimal:
    mark_price = _positive_price(deal.last_price if price is None else price)
    proceeds = deal.base_quantity * mark_price
    exit_fee = proceeds * D(str(deal.config.fee_rate_pct_per_side)) / D("100")
    slippage_rate = D(str(deal.config.slippage_pct_per_side)) / D("100")
    entry_slippage = deal.quote_spent * slippage_rate
    exit_slippage = proceeds * slippage_rate
    return proceeds - deal.quote_spent - deal.entry_fees_quote - exit_fee - entry_slippage - exit_slippage

def update_paper_deal(deal: PaperDeal, price: Decimal | str | float) -> list[PaperEvent]:
    """Advance a paper deal using one observed Spot price. Safe to call repeatedly."""
    current = _positive_price(price)
    if deal.status != "active":
        return []
    deal.last_price = current
    emitted: list[PaperEvent] = []

    # Protective exits are always evaluated first. Price Range and cooldown only
    # govern opening additional DCA orders and must never suppress risk exits.
    if deal.config.max_deal_duration_hours > 0:
        max_age = deal.config.max_deal_duration_hours * 3600
        if time.time() - deal.started_at_epoch >= max_age:
            return [_close(deal, current, "max_deal_duration")]

    avg = deal.average_entry_price
    deal.highest_price = max(deal.highest_price, current)

    stop_price: Decimal | None = None
    if deal.config.stop_loss_enabled:
        stop_basis = deal.base_price if deal.config.stop_loss_basis == "base_order" else avg
        stop_price = stop_basis * (D("1") - D(str(deal.config.stop_loss_pct)) / D("100"))
    if deal.config.move_stop_to_breakeven_enabled:
        activation = avg * (D("1") + D(str(deal.config.breakeven_activation_pct)) / D("100"))
        if current >= activation:
            deal.breakeven_active = True
        if deal.breakeven_active:
            breakeven = avg * (D("1") + D(str(deal.config.breakeven_offset_pct)) / D("100"))
            stop_price = max(stop_price or breakeven, breakeven)
    if stop_price is not None and current <= stop_price:
        emitted.append(_close(deal, current, "stop_loss_or_breakeven"))
        return emitted

    tp_basis = deal.base_price if deal.config.take_profit_basis == "base_order" else avg
    tp_price = tp_basis * (D("1") + D(str(deal.config.take_profit_pct)) / D("100"))
    if deal.config.trailing_take_profit_enabled:
        activation_pct = max(D(str(deal.config.trailing_activation_pct)), D(str(deal.config.take_profit_pct)))
        activation_price = tp_basis * (D("1") + activation_pct / D("100"))
        if current >= activation_price:
            deal.trailing_active = True
        if deal.trailing_active:
            trailing_stop = deal.highest_price * (D("1") - D(str(deal.config.trailing_callback_pct)) / D("100"))
            if current <= trailing_stop:
                emitted.append(_close(deal, current, "trailing_take_profit"))
                return emitted
    elif current >= tp_price:
        emitted.append(_close(deal, current, "take_profit"))
        return emitted

    # Price range is an entry/DCA guard, not an exit guard.
    if deal.config.price_range_enabled and not (D(str(deal.config.price_range_min)) <= current <= D(str(deal.config.price_range_max))):
        return emitted

    cooldown = deal.config.cooldown_seconds
    cooldown_ready = cooldown <= 0 or deal.last_dca_at_epoch is None or time.time() - deal.last_dca_at_epoch >= cooldown
    if cooldown_ready:
        while deal.safety_orders_filled < len(deal.safety_levels):
            level = deal.safety_levels[deal.safety_orders_filled]
            if current > level.price:
                break
            if deal.quote_spent + level.quote_amount > D(str(deal.config.max_capital_per_deal)):
                event = PaperEvent("safety_order_blocked", "Safety order skipped because it would exceed the deal capital cap", level.price, level.quote_amount, level.base_quantity)
                deal.events.append(event)
                emitted.append(event)
                deal.safety_orders_filled += 1
                continue
            fee = level.quote_amount * D(str(deal.config.fee_rate_pct_per_side)) / D("100")
            deal.quote_spent += level.quote_amount
            deal.base_quantity += level.base_quantity
            deal.entry_fees_quote += fee
            deal.safety_orders_filled += 1
            deal.last_dca_at_epoch = time.time()
            event = PaperEvent("safety_order_filled", f"Simulated safety order {level.index} filled at its limit price", level.price, level.quote_amount, level.base_quantity)
            deal.events.append(event)
            emitted.append(event)
            if cooldown > 0:
                break
    return emitted

def serialize_paper_deal(deal: PaperDeal) -> str:
    data = {
        "id": deal.id, "strategy_name": deal.strategy_name, "symbol": deal.symbol, "status": deal.status,
        "config": deal.config.model_dump(), "started_at_epoch": deal.started_at_epoch,
        "base_price": str(deal.base_price), "last_price": str(deal.last_price), "quote_spent": str(deal.quote_spent),
        "base_quantity": str(deal.base_quantity), "entry_fees_quote": str(deal.entry_fees_quote),
        "safety_levels": [{"index": x.index, "deviation_pct": str(x.deviation_pct), "price": str(x.price), "quote_amount": str(x.quote_amount), "base_quantity": str(x.base_quantity)} for x in deal.safety_levels],
        "safety_orders_filled": deal.safety_orders_filled, "highest_price": str(deal.highest_price),
        "trailing_active": deal.trailing_active, "breakeven_active": deal.breakeven_active,
        "exit_price": str(deal.exit_price) if deal.exit_price is not None else None,
        "realized_pnl_quote": str(deal.realized_pnl_quote), "close_reason": deal.close_reason,
        "last_dca_at_epoch": deal.last_dca_at_epoch,
        "events": [{"event_type": e.event_type, "detail": e.detail, "price": str(e.price), "quote_amount": str(e.quote_amount), "base_quantity": str(e.base_quantity)} for e in deal.events],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True)

def deserialize_paper_deal(payload: str) -> PaperDeal:
    data = json.loads(payload)
    config = StrategyConfig.model_validate(data["config"])
    levels = tuple(SafetyOrderLevel(index=int(x["index"]), deviation_pct=D(x["deviation_pct"]), price=D(x["price"]), quote_amount=D(x["quote_amount"]), base_quantity=D(x["base_quantity"])) for x in data["safety_levels"])
    deal = PaperDeal(
        id=str(data["id"]), strategy_name=str(data["strategy_name"]), symbol=str(data["symbol"]), status=data["status"], config=config,
        base_price=D(data["base_price"]), last_price=D(data["last_price"]), quote_spent=D(data["quote_spent"]), base_quantity=D(data["base_quantity"]),
        entry_fees_quote=D(data["entry_fees_quote"]), safety_levels=levels, started_at_epoch=float(data["started_at_epoch"]), safety_orders_filled=int(data["safety_orders_filled"]),
        highest_price=D(data["highest_price"]), trailing_active=bool(data["trailing_active"]), breakeven_active=bool(data["breakeven_active"]),
        exit_price=D(data["exit_price"]) if data["exit_price"] is not None else None, realized_pnl_quote=D(data["realized_pnl_quote"]), close_reason=data["close_reason"],
        last_dca_at_epoch=float(data["last_dca_at_epoch"]) if data.get("last_dca_at_epoch") is not None else None,
    )
    deal.events = [PaperEvent(event_type=e["event_type"], detail=e["detail"], price=D(e["price"]), quote_amount=D(e["quote_amount"]), base_quantity=D(e["base_quantity"])) for e in data["events"]]
    return deal
