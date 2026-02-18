"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Database
    database_url: str

    # External APIs
    twelve_data_api_key: str

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    # Scheduling
    candle_refresh_delay_seconds: int = 60

    # Trading
    # Prop firm account balance in USD (sourced from ACCOUNT_BALANCE env var)
    account_balance: float = 100000.0

    # Telegram (optional -- system works without these configured)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @model_validator(mode="after")
    def normalize_database_url(self) -> "Settings":
        """Ensure DATABASE_URL uses the asyncpg driver.

        Railway and other providers supply postgresql:// but SQLAlchemy async
        requires postgresql+asyncpg://.
        """
        url = self.database_url
        if url.startswith("postgresql://"):
            self.database_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            self.database_url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()
