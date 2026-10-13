from dca_bot.config import Settings


def test_app_environment_is_normalized_before_production_safeguards():
    settings = Settings(
        app_env=" Production ",
        app_secret_key="random-production-secret-" + "x" * 32,
        admin_password_hash="configured-password-hash",
        session_cookie_secure=True,
        trading_mode="paper",
        live_trading_enabled=False,
    )

    assert settings.app_env == "production"
    settings.assert_safe()


def test_app_environment_normalization_is_case_insensitive():
    assert Settings(app_env="DEVELOPMENT").app_env == "development"
