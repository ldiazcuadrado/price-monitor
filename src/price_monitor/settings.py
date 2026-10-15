"""Runtime settings and secrets, read from environment variables and ``.env``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from price_monitor import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRICE_MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    config_path: Path = Path("sites.yaml")
    db_path: Path = Path("data/price_monitor.db")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    user_agent: str = f"price-monitor/{__version__}"

    watch_interval: str = "6h"
    watch_jitter: float = 0.1

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_to: Annotated[list[str], NoDecode] = []
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"

    webhook_url: str | None = None
    webhook_bearer_token: SecretStr | None = None

    @field_validator("smtp_to", mode="before")
    @classmethod
    def _split_recipients(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value
