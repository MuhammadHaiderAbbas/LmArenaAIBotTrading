"""Application configuration.

All settings are read from environment variables (12-factor). Secrets are never
hardcoded: docker-compose supplies development values via .env.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Identity -----------------------------------------------------------
    PROJECT_NAME: str = "AUREX AI"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Environment = "development"
    DEBUG: bool = True

    SECRET_KEY: str = Field(
        default="dev-only-insecure-key-change-me-in-production-0123456789",
        min_length=32,
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    PASSWORD_MIN_LENGTH: int = 10

    # --- Database -----------------------------------------------------------
    DATABASE_URL: str = "sqlite:///./aurex_dev.db"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5

    # --- Cache / events (defined in Phase 1, consumed from Phase 3) ---------
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- CORS ---------------------------------------------------------------
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ]

    # --- Bootstrap ----------------------------------------------------------
    FIRST_ADMIN_EMAIL: str = "admin@aurex.ai"
    FIRST_ADMIN_PASSWORD: str = "ChangeMe!2026"
    DEFAULT_PAPER_BALANCE: float = 100_000.00
    BASE_CURRENCY: str = "GBP"

    # --- Product guardrails (blueprint section 18) --------------------------
    LIVE_TRADING_ENABLED: bool = False
    REAL_MONEY_ENABLED: bool = False
    MAX_LOGIN_ATTEMPTS_PER_MINUTE: int = 10

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
