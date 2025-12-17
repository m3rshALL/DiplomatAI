from __future__ import annotations

from functools import lru_cache
from typing import Optional
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    perplexity_api_key: Optional[str] = Field(default=None, alias="PERPLEXITY_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    webapp_url: Optional[str] = Field(default=None, alias="WEBAPP_URL")

    database_url: Optional[str] = Field(default=None, alias="DATABASE_URL")
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")
    
    @model_validator(mode="after")
    def validate_required_fields(self) -> "Settings":
        missing_fields = []
        if not self.telegram_bot_token:
            missing_fields.append("TELEGRAM_BOT_TOKEN")
        if not self.perplexity_api_key:
            missing_fields.append("PERPLEXITY_API_KEY")
        if not self.openai_api_key:
            missing_fields.append("OPENAI_API_KEY")
        if not self.database_url:
            missing_fields.append("DATABASE_URL")
        if not self.redis_url:
            missing_fields.append("REDIS_URL")
        
        if missing_fields:
            raise ValueError(
                f"Отсутствуют обязательные переменные окружения: {', '.join(missing_fields)}. "
                f"Создайте файл .env на основе env.example и заполните необходимые значения."
            )
        
        return self

    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")

    perplexity_endpoint: str = Field(
        default="https://api.perplexity.ai/chat/completions",
        alias="PERPLEXITY_ENDPOINT",
    )
    perplexity_model: str = Field(default="sonar-pro", alias="PERPLEXITY_MODEL")
    perplexity_use_response_format: bool = Field(default=True, alias="PERPLEXITY_USE_RESPONSE_FORMAT")

    # MVP limits / TTLs
    free_daily_limit: int = Field(default=3, alias="FREE_DAILY_LIMIT")
    disable_rate_limit: bool = Field(default=False, alias="DISABLE_RATE_LIMIT")
    perplexity_cache_ttl_seconds: int = Field(default=30 * 60, alias="PERPLEXITY_CACHE_TTL_SECONDS")
    session_max_turns: int = Field(default=3, alias="SESSION_MAX_TURNS")
    session_ttl_seconds: int = Field(default=7 * 24 * 3600, alias="SESSION_TTL_SECONDS")

    perplexity_timeout_seconds: float = Field(default=30.0, alias="PERPLEXITY_TIMEOUT_SECONDS")
    openai_timeout_seconds: float = Field(default=60.0, alias="OPENAI_TIMEOUT_SECONDS")

    # Retry policy
    retry_max_attempts: int = Field(default=4, alias="RETRY_MAX_ATTEMPTS")
    retry_base_delay_seconds: float = Field(default=0.7, alias="RETRY_BASE_DELAY_SECONDS")
    retry_max_delay_seconds: float = Field(default=8.0, alias="RETRY_MAX_DELAY_SECONDS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


