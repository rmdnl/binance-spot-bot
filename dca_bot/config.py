from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server settings. Strategy configuration is persisted separately."""
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_host: str = '127.0.0.1'
    app_port: int = Field(default=8082, ge=1, le=65535)
    app_env: str = 'development'
    app_secret_key: str = 'development-only-change-this-secret'
    admin_password_hash: str = ''
    database_url: str = 'sqlite:///./data/dca_bot.sqlite3'
    trading_mode: str = 'paper'
    live_trading_enabled: bool = False
    binance_api_key: str = ''
    binance_api_secret: str = ''
    binance_testnet: bool = True
    session_cookie_secure: bool = False
    session_max_age_seconds: int = Field(default=3600, ge=300, le=86400)
    paper_poll_interval_seconds: int = Field(default=10, ge=3, le=300)

    def assert_safe(self) -> None:
        # Exchange execution is intentionally not implemented. Reject any setting
        # that could imply a live-capable deployment, regardless of app environment.
        if self.trading_mode.lower() != 'paper':
            raise ValueError('Only TRADING_MODE=paper is supported; exchange execution is not implemented')
        if self.live_trading_enabled:
            raise ValueError('LIVE_TRADING_ENABLED must remain false until live execution is separately implemented and reviewed')

        if self.app_env.lower() == 'production':
            if (
                len(self.app_secret_key) < 32
                or self.app_secret_key == 'development-only-change-this-secret'
                or self.app_secret_key == 'replace-with-a-long-random-secret'
            ):
                raise ValueError('APP_SECRET_KEY must be a unique random secret of at least 32 chars in production')
            if not self.admin_password_hash:
                raise ValueError('ADMIN_PASSWORD_HASH must be configured in production')
            if not self.session_cookie_secure:
                raise ValueError('SESSION_COOKIE_SECURE must be true in production behind HTTPS')
            if self.trading_mode == 'live' and not self.live_trading_enabled:
                raise ValueError('Live mode requires LIVE_TRADING_ENABLED=true')


@lru_cache
def get_settings() -> Settings:
    return Settings()
