from functools import lru_cache
from pathlib import Path
from decimal import Decimal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")
    dashboard_password_hash: str = ""
    dashboard_session_secret: str = ""
    dashboard_cookie_secure: bool = True
    dashboard_min_password_length: int = Field(default=8, ge=8)
    dashboard_session_max_age_seconds: int = Field(default=3600, ge=300, le=86400)
    dashboard_login_max_attempts: int = Field(default=5, ge=3, le=20)
    dashboard_login_window_seconds: int = Field(default=300, ge=60, le=3600)
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""
    binance_live_api_key: str = ""
    binance_live_api_secret: str = ""
    database_url: str = "sqlite:///./data/spot_bot.db"
    default_environment: str = "testnet"
    allow_order_submission: bool = False
    grid_min_net_profit_pct: Decimal = Field(default=Decimal("0.30"), ge=Decimal("0"))
    grid_maker_fee_pct: Decimal = Field(default=Decimal("0.10"), ge=Decimal("0"))
    grid_slippage_pct: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0"))

    def credentials(self, environment: str):
        env = environment.lower().strip()
        if env == "testnet": return self.binance_testnet_api_key, self.binance_testnet_api_secret
        if env == "live": return self.binance_live_api_key, self.binance_live_api_secret
        raise ValueError("environment must be testnet or live")

    def validate_dashboard_security(self):
        if not self.dashboard_password_hash or len(self.dashboard_session_secret) < 32:
            raise ValueError("Dashboard security secrets are not configured")

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
