"""Typed settings loaded from environment. One canonical source for config."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    postgres_port: int = 5432

    pgbouncer_host: str = "pgbouncer"
    pgbouncer_port: int = 6432

    celery_broker_url: str
    celery_result_backend: str

    log_level: str = "INFO"
    log_format: str = "json"

    sentry_dsn: str = ""

    universe_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"]
    )
    universe_timeframes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["1m", "5m"]
    )

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True

    @field_validator("universe_symbols", "universe_timeframes", mode="before")
    @classmethod
    def split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def pgbouncer_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.pgbouncer_host}:{self.pgbouncer_port}/{self.postgres_db}"
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
