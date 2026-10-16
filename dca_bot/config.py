from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""
    binance_live_api_key: str = ""
    binance_live_api_secret: str = ""
    dashboard_password_hash: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/spot_terminal.db"

    dashboard_min_password_length: int = Field(default=8, ge=8)

    def credentials(self, environment: str) -> tuple[str, str]:
        env = environment.strip().lower()
        if env == "testnet":
            return self.binance_testnet_api_key, self.binance_testnet_api_secret
        if env == "live":
            return self.binance_live_api_key, self.binance_live_api_secret
        raise ValueError("environment must be 'testnet' or 'live'")
