from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Sequence

from .strategy import StrategyConfig


@dataclass(frozen=True)
class MarketIndicators:
    close: float
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    atr_pct: float
    adx: float


@dataclass(frozen=True)
class AutonomousDecision:
    symbol: str
    timeframe: str
    action: str
    reason: str
    score: int
    indicators: MarketIndicators
    parameters: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "action": self.action,
            "reason": self.reason,
            "score": self.score,
            "indicators": asdict(self.indicators),
            "parameters": self.parameters,
            "execution": "disabled",
        }


def _ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"At least {period} candles are required for EMA")
    alpha = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(closes: Sequence[float], period: int) -> float:
    if len(closes) <= period:
        raise ValueError(f"At least {period + 1} candles are required for RSI")
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr_adx(candles: Sequence[Sequence[float]], period: int) -> tuple[float, float]:
    if len(candles) < period + 1:
        raise ValueError(f"At least {period + 1} candles are required for ATR/ADX")
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        high, low, close = float(current[2]), float(current[3]), float(previous[4])
        prev_high, prev_low = float(previous[2]), float(previous[3])
        true_ranges.append(max(high - low, abs(high - close), abs(low - close)))
        up = high - prev_high
        down = prev_low - low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    def wilder(values: Sequence[float]) -> list[float]:
        if len(values) < period:
            return []
        current_value = sum(values[:period]) / period
        output = [current_value]
        for value in values[period:]:
            current_value = ((current_value * (period - 1)) + value) / period
            output.append(current_value)
        return output

    atr_values = wilder(true_ranges)
    plus_values = wilder(plus_dm)
    minus_values = wilder(minus_dm)
    if not atr_values or not plus_values or not minus_values:
        raise ValueError("Not enough candles to calculate ATR/ADX")
    dx_values = []
    for atr, plus, minus in zip(atr_values, plus_values, minus_values):
        if atr <= 0:
            dx_values.append(0.0)
            continue
        plus_di = 100.0 * plus / atr
        minus_di = 100.0 * minus / atr
        denominator = plus_di + minus_di
        dx_values.append(100.0 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    adx_series = wilder(dx_values)
    return atr_values[-1], adx_series[-1] if adx_series else sum(dx_values[-period:]) / min(period, len(dx_values))


def calculate_indicators(
    candles: Sequence[Sequence[float]], config: StrategyConfig
) -> MarketIndicators:
    """Calculate deterministic indicators from CCXT OHLCV candles."""
    minimum = max(config.ema_slow + 1, config.rsi_period + 1, config.atr_period * 2 + 1)
    if len(candles) < minimum:
        raise ValueError(f"At least {minimum} OHLCV candles are required")
    validated: list[tuple[float, float, float, float, float, float]] = []
    for candle in candles:
        if len(candle) < 6:
            raise ValueError("Candles must contain OHLCV fields")
        try:
            timestamp, open_price, high, low, close_price, volume = (float(value) for value in candle[:6])
        except (TypeError, ValueError) as exc:
            raise ValueError("Candles contain non-numeric OHLCV values") from exc
        values = (timestamp, open_price, high, low, close_price, volume)
        if any(not isfinite(value) for value in values):
            raise ValueError("Candles contain non-finite OHLCV values")
        if min(open_price, high, low, close_price) <= 0 or volume < 0:
            raise ValueError("Candles contain invalid OHLCV values")
        if high < max(open_price, low, close_price) or low > min(open_price, high, close_price):
            raise ValueError("Candles contain inconsistent high/low prices")
        validated.append(values)
    closes = [candle[4] for candle in validated]
    close = closes[-1]
    ema_fast = _ema(closes, config.ema_fast)
    ema_slow = _ema(closes, config.ema_slow)
    rsi = _rsi(closes, config.rsi_period)
    atr, adx = _atr_adx(candles, config.atr_period)
    return MarketIndicators(
        close=close,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi=rsi,
        atr=atr,
        atr_pct=(atr / close) * 100.0,
        adx=adx,
    )


def _bounded(value: float, low: float, high: float) -> float:
    return round(max(low, min(high, value)), 4)


def analyze_market(
    symbol: str,
    timeframe: str,
    candles: Sequence[Sequence[float]],
    config: StrategyConfig,
) -> AutonomousDecision:
    """Return a long-only Spot decision and risk-bounded adaptive DCA parameters.

    This function is advisory only: it never places or queues an exchange order.
    """
    normalized = symbol.strip().upper().replace("-", "/")
    if "/" not in normalized and normalized.endswith("USDT"):
        normalized = f"{normalized[:-4]}/USDT"
    if normalized not in config.symbols:
        config = config.model_copy(update={"symbols": [normalized]})
    indicators = calculate_indicators(candles, config)

    trend_up = indicators.close > indicators.ema_slow and indicators.ema_fast > indicators.ema_slow
    # DCA-first policy: indicators may delay an overheated/high-volatility entry,
    # but a bearish trend alone must not disable averaging-down behavior.
    # Once a deal starts, safety orders are controlled by the DCA price ladder,
    # not by repeatedly re-running these entry indicators.
    pullback_ok = indicators.rsi <= config.rsi_max_entry
    trend_strength_ok = indicators.adx >= config.adx_min
    score = 0
    score += 25 if pullback_ok else 0
    score += 20 if indicators.atr_pct <= 5.0 else 0
    score += 15 if trend_up else 0
    score += 10 if indicators.close >= indicators.ema_fast else 0
    score += 10 if trend_strength_ok else 0

    if indicators.atr_pct > 8.0:
        action, reason = "wait", "Extreme volatility exceeds the autonomous entry safety threshold"
    elif indicators.atr_pct > 5.0:
        action, reason = "wait", "Volatility is too high to safely start a new DCA deal"
    elif not pullback_ok:
        action, reason = "wait", "RSI exceeds the configured maximum for entry; waiting for a pullback"
    elif not trend_strength_ok:
        action, reason = "wait", "ADX is below the configured minimum for entry"
    else:
        action, reason = "candidate", "DCA-first entry accepted; downside safety orders will average the position by price levels (paper only)"

    # Volatility-aware spacing and TP, bounded to prevent unbounded parameter drift.
    initial_deviation = _bounded(indicators.atr_pct * 1.25, 0.5, 3.0)
    deviation_scale = _bounded(1.2 + indicators.atr_pct / 12.0, 1.2, 1.8)
    minimum_net_target_pct = (
        config.min_net_profit_pct
        + 2 * config.fee_rate_pct_per_side
        + 2 * config.slippage_pct_per_side
    )
    # Keep ordinary targets close enough to market to encourage completed cycles.
    # The 5% hard ceiling is only available when the configured net-profit floor
    # plus estimated round-trip costs genuinely requires a target above 3%.
    adaptive_base_target = _bounded(
        max(config.take_profit_pct, indicators.atr_pct * 0.8),
        0.5,
        3.0,
    )
    if minimum_net_target_pct > 5.0:
        action = "wait"
        reason = (
            "Minimum net-profit requirement plus estimated fees/slippage exceeds "
            "the 5% take-profit hard cap"
        )
        take_profit = 5.0
    else:
        take_profit = _bounded(
            max(adaptive_base_target, minimum_net_target_pct),
            0.5,
            5.0,
        )
    safe_count = min(config.max_safety_orders, 8)
    parameters: dict[str, object] = {
        "initial_deviation_pct": initial_deviation,
        "deviation_scale": deviation_scale,
        "take_profit_pct": take_profit,
        "max_safety_orders": safe_count,
        "safety_order_volume_scale": _bounded(config.safety_order_volume_scale, 1.0, 2.0),
        "timeframe": timeframe,
        "capital_cap_quote": config.max_capital_per_deal,
        "max_total_allocation_quote": config.max_total_allocation,
        "fee_rate_pct_per_side": config.fee_rate_pct_per_side,
        "slippage_pct_per_side": config.slippage_pct_per_side,
    }
    # Reduce the ladder until the estimate fits the configured per-deal hard cap.
    while safe_count > 0:
        candidate = config.model_copy(update={
            "symbols": [normalized],
            "initial_deviation_pct": initial_deviation,
            "deviation_scale": deviation_scale,
            "take_profit_pct": take_profit,
            "max_safety_orders": safe_count,
            "safety_order_volume_scale": float(parameters["safety_order_volume_scale"]),
        })
        try:
            required = candidate.estimated_max_deal_capital(indicators.close)
        except ValueError:
            required = float("inf")
        if required <= config.max_capital_per_deal and required <= config.max_total_allocation:
            break
        safe_count -= 1
    candidate = config.model_copy(update={
        "symbols": [normalized],
        "initial_deviation_pct": initial_deviation,
        "deviation_scale": deviation_scale,
        "take_profit_pct": take_profit,
        "max_safety_orders": safe_count,
        "safety_order_volume_scale": float(parameters["safety_order_volume_scale"]),
    })
    try:
        required = candidate.estimated_max_deal_capital(indicators.close)
    except ValueError:
        required = float("inf")
    parameters["max_safety_orders"] = safe_count
    parameters["estimated_deal_capital_quote"] = round(required, 8)
    parameters["capital_safe"] = required <= config.max_capital_per_deal and required <= config.max_total_allocation
    if not parameters["capital_safe"]:
        action, reason = "wait", "Base order or minimum ladder exceeds the configured capital cap"
    parameters["score"] = score
    return AutonomousDecision(
        symbol=normalized,
        timeframe=timeframe,
        action=action,
        reason=reason,
        score=score,
        indicators=indicators,
        parameters=parameters,
    )
