from pathlib import Path

import pytest

from dca_bot.config import Settings


def test_production_rejects_known_public_example_secret():
    settings = Settings(
        app_env="production",
        app_secret_key="replace-with-a-long-random-secret",
        admin_password_hash="configured-hash",
        session_cookie_secure=True,
    )

    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        settings.assert_safe()


@pytest.mark.parametrize(
    ("trading_mode", "live_enabled", "pattern"),
    [
        ("live", False, "TRADING_MODE"),
        ("live", True, "TRADING_MODE"),
        ("testnet", False, "BINANCE_TESTNET"),
        ("paper", True, "LIVE_TRADING_ENABLED"),
    ],
)
def test_runtime_rejects_unsafe_configuration(trading_mode, live_enabled, pattern):
    settings = Settings(trading_mode=trading_mode, live_trading_enabled=live_enabled)

    with pytest.raises(ValueError, match=pattern):
        settings.assert_safe()


def test_production_accepts_unique_secret_with_paper_mode():
    settings = Settings(
        app_env="production",
        app_secret_key="a-unique-production-secret-that-is-not-the-example",
        admin_password_hash="configured-hash",
        session_cookie_secure=True,
        trading_mode="paper",
        live_trading_enabled=False,
    )

    settings.assert_safe()


def test_env_example_has_no_inline_comment_on_environment_assignment():
    env_example = Path(__file__).parents[1].joinpath(".env.example").read_text()
    assignment_lines = [
        line for line in env_example.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    ]

    assert all(" #" not in line for line in assignment_lines)
