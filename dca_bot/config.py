from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Compatibility settings retained only while the legacy DCA test modules are removed.
    app_env: str = "development"
    app_secret_key: str = ""
    admin_password_hash: str = ""
    session_cookie_secure: bool = True
    trading_mode: str = "paper"
    live_trading_enabled: bool = False
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    paper_poll_interval_seconds: int = 10

    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""
    binance_live_api_key: str = ""
    binance_live_api_secret: str = ""
    dashboard_password_hash: str = ""
    dashboard_session_secret: str = ""
    dashboard_cookie_secure: bool = True
    database_url: str = "sqlite+aiosqlite:///./data/spot_terminal.db"
    dashboard_min_password_length: int = Field(default=8, ge=8)
    dashboard_session_max_age_seconds: int = Field(default=3600, ge=300, le=86400)
    dashboard_login_max_attempts: int = Field(default=5, ge=3, le=20)
    dashboard_login_window_seconds: int = Field(default=300, ge=60, le=3600)

    def credentials(self, environment: str) -> tuple[str, str]:
        env = environment.strip().lower()
        if env == "testnet":
            key, secret = self.binance_testnet_api_key, self.binance_testnet_api_secret
        elif env == "live":
            key, secret = self.binance_live_api_key, self.binance_live_api_secret
        else:
            raise ValueError("environment must be 'testnet' or 'live'")
        if not key or not secret:
            raise ValueError(f"Binance {env} credentials are not configured")
        return key, secret

    def assert_safe(self) -> None:
        # Legacy tests expect the historical runtime to stay paper-only.
        if self.trading_mode != "paper" or self.live_trading_enabled:
            raise ValueError("TRADING_MODE=paper and LIVE_TRADING_ENABLED=false are required")
        if self.app_env == "production" and (not self.app_secret_key or self.app_secret_key == "replace-with-a-long-random-secret"):
            raise ValueError("APP_SECRET_KEY must be unique in production")

    def validate_dashboard_security(self) -> None:
        if not self.dashboard_password_hash.strip() and not self.admin_password_hash.strip():
            raise ValueError("DASHBOARD_PASSWORD_HASH is required")
        if len(self.dashboard_session_secret) < 32:
            raise ValueError("DASHBOARD_SESSION_SECRET must be at least 32 characters")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
