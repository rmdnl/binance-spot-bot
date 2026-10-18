from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Environment, InventoryPosition, RealizedPNLEntry, SpotFill

D = Decimal
ZERO = D("0")


def _last_price(adapter, symbol: str) -> D | None:
    try:
        if symbol not in adapter.exchange.markets:
            return None
        raw = adapter.ticker(symbol)
        value = raw.get("last") if raw else None
        price = D(str(value)) if value is not None else ZERO
        return price if price.is_finite() and price > ZERO else None
    except Exception:
        return None


def _quote_to_usdt(adapter, quote: str) -> D | None:
    quote = quote.upper()
    if quote == "USDT":
        return D("1")
    return _last_price(adapter, f"{quote}/USDT")


def dashboard_metrics(
    db: Session,
    adapter,
    environment: Environment,
    *,
    balance_snapshot: dict | None = None,
) -> dict:
    """Calculate equity and ledger PNL in USDT without silently pricing unknown assets at zero.

    Reuse a balance snapshot when the caller already fetched one, avoiding a
    second exchange request that could fail independently or show a different
    account state.
    """
    raw = balance_snapshot if balance_snapshot is not None else adapter.balance()
    totals = raw.get("total", {}) or {}
    equity = ZERO
    unpriced_assets: list[str] = []

    for asset, amount_raw in totals.items():
        amount = D(str(amount_raw or "0"))
        if not amount.is_finite():
            unpriced_assets.append(asset)
            continue
        if amount <= ZERO:
            continue
        conversion = _quote_to_usdt(adapter, asset)
        if conversion is None:
            unpriced_assets.append(asset)
            continue
        equity += amount * conversion

    # Old fills that paid a third-token fee without a persisted quote valuation
    # cannot be reconstructed exactly from current prices. Flag PNL as partial
    # instead of pretending those historical costs were zero.
    unvalued_third_fee_fill_ids: list[int] = []
    fills = db.scalars(select(SpotFill).where(SpotFill.environment == environment.value)).all()
    for fill in fills:
        try:
            base, quote = fill.symbol.upper().split("/", 1)
        except ValueError:
            unvalued_third_fee_fill_ids.append(fill.id)
            continue
        fee_currency = (fill.fee_currency or "").upper()
        if D(str(fill.fee or "0")) > ZERO and fee_currency not in {base, quote} and fill.fee_quote_value is None:
            unvalued_third_fee_fill_ids.append(fill.id)

    realized = ZERO
    realized_unpriced: set[str] = set()
    entries = db.scalars(
        select(RealizedPNLEntry).where(RealizedPNLEntry.environment == environment.value)
    ).all()
    for entry in entries:
        quote = entry.symbol.upper().split("/", 1)[1]
        conversion = _quote_to_usdt(adapter, quote)
        if conversion is None:
            realized_unpriced.add(quote)
            continue
        realized += D(str(entry.realized_pnl)) * conversion

    unrealized = ZERO
    unrealized_unpriced: set[str] = set()
    positions = db.scalars(
        select(InventoryPosition).where(
            InventoryPosition.environment == environment.value,
            InventoryPosition.base_quantity > 0,
        )
    ).all()
    for position in positions:
        price = _last_price(adapter, position.symbol)
        conversion = _quote_to_usdt(adapter, position.quote_asset)
        if price is None or conversion is None:
            unrealized_unpriced.add(position.symbol)
            continue
        pnl_quote = D(str(position.base_quantity)) * price - D(str(position.quote_cost))
        unrealized += pnl_quote * conversion

    return {
        "equity_usdt": str(equity),
        "equity_complete": not unpriced_assets,
        "unpriced_assets": sorted(unpriced_assets),
        "realized_pnl_usdt": str(realized),
        "realized_pnl_complete": not realized_unpriced and not unvalued_third_fee_fill_ids,
        "unvalued_third_fee_fill_ids": sorted(unvalued_third_fee_fill_ids),
        "realized_pnl_valuation_basis": "current_market_quote_conversion",
        "unpriced_realized_quotes": sorted(realized_unpriced),
        "unrealized_pnl_usdt": str(unrealized),
        "unrealized_pnl_complete": not unrealized_unpriced and not unvalued_third_fee_fill_ids,
        "unpriced_positions": sorted(unrealized_unpriced),
    }
