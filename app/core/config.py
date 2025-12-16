from __future__ import annotations

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    perplexity_api_key: str = Field(alias="PERPLEXITY_API_KEY")
    openai_api_key: str = Field(alias="OPENAI_API_KEY")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")

    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")

    perplexity_endpoint: str = Field(
        default="https://api.perplexity.ai/chat/completions",
        alias="PERPLEXITY_ENDPOINT",
    )
    perplexity_model: str = Field(default="sonar-pro", alias="PERPLEXITY_MODEL")
    perplexity_use_response_format: bool = Field(default=True, alias="PERPLEXITY_USE_RESPONSE_FORMAT")

    # MVP limits / TTLs
    free_daily_limit: int = Field(default=3, alias="FREE_DAILY_LIMIT")
    perplexity_cache_ttl_seconds: int = Field(default=30 * 60, alias="PERPLEXITY_CACHE_TTL_SECONDS")

    perplexity_timeout_seconds: float = Field(default=30.0, alias="PERPLEXITY_TIMEOUT_SECONDS")
    openai_timeout_seconds: float = Field(default=60.0, alias="OPENAI_TIMEOUT_SECONDS")

    # Retry policy
    retry_max_attempts: int = Field(default=4, alias="RETRY_MAX_ATTEMPTS")
    retry_base_delay_seconds: float = Field(default=0.7, alias="RETRY_BASE_DELAY_SECONDS")
    retry_max_delay_seconds: float = Field(default=8.0, alias="RETRY_MAX_DELAY_SECONDS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


