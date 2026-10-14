from __future__ import annotations

import json
from typing import Any

from .models import StrategyRecord
from .strategy import StrategyConfig


class StrategyRuntimeError(ValueError):
    """Raised when a persisted strategy cannot be used safely at runtime."""


def load_strategy_config(record: StrategyRecord) -> StrategyConfig:
    if not record.enabled:
        raise StrategyRuntimeError("strategy is disabled")
    try:
        raw: Any = json.loads(record.config_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise StrategyRuntimeError("strategy config is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise StrategyRuntimeError("strategy config must be a JSON object")
    raw = dict(raw)
    raw.setdefault("name", record.name)
    raw["enabled"] = True
    try:
        return StrategyConfig.model_validate(raw)
    except Exception as exc:
        raise StrategyRuntimeError("strategy config failed validation") from exc
