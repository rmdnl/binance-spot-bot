from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.sh"


def test_preflight_script_has_required_production_guards():
    text = PREFLIGHT.read_text(encoding="utf-8")
    required = [
        'mode=$(stat -c \'%a\' .env',
        '[[ "$mode" == "600" ]]',
        'settings.app_env != "production"',
        'settings.trading_mode != "paper"',
        'settings.live_trading_enabled',
        'not settings.binance_testnet',
        'settings.app_host not in {"127.0.0.1", "localhost"}',
        'not settings.session_cookie_secure',
        'python -m compileall -q dca_bot',
        'python -m pytest -q',
    ]
    for needle in required:
        assert needle in text
