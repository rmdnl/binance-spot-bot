import json

import pytest

from dca_bot.models import StrategyRecord
from dca_bot.strategy_runtime import StrategyRuntimeError, load_strategy_config


def test_loads_valid_enabled_strategy():
    record = StrategyRecord(name="auto", config_json=json.dumps({"symbols": ["BTCUSDT"], "pair_selection_mode": "automatic_usdt"}), enabled=True)
    config = load_strategy_config(record)
    assert config.name == "auto"
    assert config.symbols == ["BTC/USDT"]
    assert config.pair_selection_mode == "automatic_usdt"
    assert config.enabled is True


def test_disabled_strategy_fails_closed():
    record = StrategyRecord(name="off", config_json="{}", enabled=False)
    with pytest.raises(StrategyRuntimeError, match="disabled"):
        load_strategy_config(record)


def test_invalid_json_fails_closed():
    record = StrategyRecord(name="broken", config_json="{", enabled=True)
    with pytest.raises(StrategyRuntimeError, match="valid JSON"):
        load_strategy_config(record)


def test_non_object_config_fails_closed():
    record = StrategyRecord(name="broken", config_json="[]", enabled=True)
    with pytest.raises(StrategyRuntimeError, match="JSON object"):
        load_strategy_config(record)
