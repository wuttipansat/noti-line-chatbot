from datetime import time
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "noti"
    environment: Literal["dev", "test", "prod"] = "dev"
    default_timezone: str = "Asia/Bangkok"

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://planner:planner@127.0.0.1:15432/planner"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
    readiness_timeout_seconds: float = 2.0

    line_channel_secret: SecretStr | None = None
    line_channel_access_token: SecretStr | None = None
    line_api_base_url: str = "https://api.line.me"
    daily_digest_time: time = time(7, 0)

    llm_provider: Literal["mock", "cloud"] = "mock"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gemini-3.1-flash-lite"
    llm_json_mode: Literal["json_schema", "json_object"] = "json_schema"
    llm_timeout_seconds: float = 20.0
    rate_limit_messages: int = 20  # per user, per window
    rate_limit_window_minutes: int = 10
    ai_daily_limit_per_user: int = 20

    @field_validator("default_timezone")
    @classmethod
    def timezone_validator(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value

    @field_validator(
        "line_channel_secret", "line_channel_access_token", "llm_api_key", mode="before"
    )
    @classmethod
    def line_validator(cls, value):
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
