from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_NAME", "telegram-edge-relay")
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_PORT", "8080")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PATH_SECRET", "webhook-secret-value")
    monkeypatch.setenv("BACKEND_BASE_URL", "https://backend.example")
    monkeypatch.setenv("BACKEND_FORWARD_PATH", "/internal/inbound/telegram-update")
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "test-shared-secret")
    monkeypatch.setenv("SIGNATURE_TTL_SECONDS", "300")
    monkeypatch.setenv("TELEGRAM_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("BACKEND_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_health_returns_safe_summary_with_version(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "telegram-edge-relay",
        "version": "1.2.3",
        "config": {
            "host": "127.0.0.1",
            "port": 8080,
            "log_level": "INFO",
            "debug": False,
            "signature_ttl_seconds": 300,
            "telegram_timeout_seconds": 10.0,
            "telegram_outbound_mode": "mixed",
            "telegram_response_mode": "transparent",
            "telegram_photo_max_bytes": None,
            "backend_timeout_seconds": 10.0,
            "telegram_bot_token_configured": True,
            "telegram_webhook_path_secret_configured": True,
            "backend_forwarding_configured": True,
            "internal_shared_secret_configured": True,
        },
    }


def test_version_returns_app_identity(client: TestClient) -> None:
    response = client.get("/version")

    assert response.status_code == 200
    assert response.json() == {
        "app_name": "telegram-edge-relay",
        "version": "1.2.3",
    }


def test_create_app_fails_fast_on_missing_required_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "APP_NAME",
        "APP_VERSION",
        "APP_HOST",
        "APP_PORT",
        "LOG_LEVEL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_PATH_SECRET",
        "BACKEND_BASE_URL",
        "BACKEND_FORWARD_PATH",
        "INTERNAL_SHARED_SECRET",
        "SIGNATURE_TTL_SECONDS",
        "TELEGRAM_TIMEOUT_SECONDS",
        "TELEGRAM_PHOTO_MAX_BYTES",
        "BACKEND_TIMEOUT_SECONDS",
        "DEBUG",
    ):
        monkeypatch.delenv(key, raising=False)

    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="invalid application configuration"):
        create_app()
    get_settings.cache_clear()
