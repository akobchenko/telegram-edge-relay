from __future__ import annotations

import os
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)


def _default_version() -> str:
    try:
        return version("telegram-edge-relay")
    except PackageNotFoundError:
        return "0.1.0"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    app_name: str = Field(default="telegram-edge-relay", alias="APP_NAME")
    app_version: str = Field(default_factory=_default_version, alias="APP_VERSION")
    app_host: str = Field(alias="APP_HOST", min_length=1)
    app_port: int = Field(alias="APP_PORT", ge=1, le=65535)
    log_level: str = Field(alias="LOG_LEVEL")
    telegram_bot_token: SecretStr = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_path_secret: SecretStr = Field(alias="TELEGRAM_WEBHOOK_PATH_SECRET")
    backend_base_url: AnyHttpUrl = Field(alias="BACKEND_BASE_URL")
    backend_forward_path: str = Field(alias="BACKEND_FORWARD_PATH", min_length=1)
    internal_shared_secret: SecretStr = Field(alias="INTERNAL_SHARED_SECRET")
    signature_ttl_seconds: int = Field(alias="SIGNATURE_TTL_SECONDS", ge=1, le=3600)
    telegram_timeout_seconds: float = Field(alias="TELEGRAM_TIMEOUT_SECONDS", ge=1.0, le=60.0)
    telegram_photo_max_bytes: int | None = Field(
        default=None,
        alias="TELEGRAM_PHOTO_MAX_BYTES",
        ge=1,
    )
    backend_timeout_seconds: float = Field(alias="BACKEND_TIMEOUT_SECONDS", ge=1.0, le=60.0)
    debug: bool = Field(alias="DEBUG")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed_levels:
            raise ValueError(f"unsupported log level: {value}")
        return normalized

    @field_validator("backend_forward_path")
    @classmethod
    def validate_backend_forward_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("BACKEND_FORWARD_PATH must start with '/'")
        return value

    @field_validator(
        "telegram_bot_token",
        "telegram_webhook_path_secret",
        "internal_shared_secret",
        mode="after",
    )
    @classmethod
    def validate_secret_lengths(cls, value: SecretStr, info) -> SecretStr:
        raw_value = value.get_secret_value()
        minimum_lengths = {
            "telegram_bot_token": 10,
            "telegram_webhook_path_secret": 16,
            "internal_shared_secret": 16,
        }
        if len(raw_value) < minimum_lengths[info.field_name]:
            raise ValueError(f"{info.field_name} is too short")
        return value

    @field_validator("backend_base_url")
    @classmethod
    def validate_backend_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.query or value.fragment:
            raise ValueError("BACKEND_BASE_URL must not include query or fragment")
        return value

    def safe_health_summary(self) -> HealthConfigSummary:
        return HealthConfigSummary(
            host=self.app_host,
            port=self.app_port,
            log_level=self.log_level,
            debug=self.debug,
            signature_ttl_seconds=self.signature_ttl_seconds,
            telegram_timeout_seconds=self.telegram_timeout_seconds,
            telegram_photo_max_bytes=self.telegram_photo_max_bytes,
            backend_timeout_seconds=self.backend_timeout_seconds,
            telegram_bot_token_configured=True,
            telegram_webhook_path_secret_configured=True,
            backend_forwarding_configured=True,
            internal_shared_secret_configured=True,
        )


class HealthConfigSummary(BaseModel):
    host: str
    port: int
    log_level: str
    debug: bool
    signature_ttl_seconds: int
    telegram_timeout_seconds: float
    telegram_photo_max_bytes: int | None
    backend_timeout_seconds: float
    telegram_bot_token_configured: bool
    telegram_webhook_path_secret_configured: bool
    backend_forwarding_configured: bool
    internal_shared_secret_configured: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw_values = dict(os.environ)
    try:
        settings = Settings.model_validate(raw_values)
    except ValidationError as exc:
        raise RuntimeError(f"invalid application configuration: {exc}") from exc
    return settings
