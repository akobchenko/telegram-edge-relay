from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.security.signing import (
    INTERNAL_SIGNATURE_HEADER,
    INTERNAL_TIMESTAMP_HEADER,
    build_internal_signature,
)
from app.services.telegram_client import TelegramClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def signed_headers(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    current_timestamp = str(timestamp or int(time.time()))
    return {
        INTERNAL_TIMESTAMP_HEADER: current_timestamp,
        INTERNAL_SIGNATURE_HEADER: build_internal_signature(
            secret=secret,
            timestamp=current_timestamp,
            body=body,
        ),
    }


def test_send_message_rejects_invalid_signature(client: TestClient) -> None:
    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")

    response = client.post(
        "/internal/telegram/sendMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("wrong-secret", body),
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "ok": False,
        "error_type": "auth_error",
        "message": "invalid internal signature",
        "status_code": 401,
        "telegram_status_code": None,
        "telegram_error_code": None,
        "telegram_description": None,
        "telegram_response": None,
        "details": None,
    }


def test_send_message_rejects_invalid_body(client: TestClient) -> None:
    raw_body = json.dumps({"chat_id": 1}).encode("utf-8")

    response = client.post(
        "/internal/telegram/sendMessage",
        content=raw_body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", raw_body),
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error_type"] == "validation_error"
    detail = payload["details"]
    assert detail[0]["loc"] == ["body", "text"]


def test_send_message_returns_mocked_telegram_success(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendMessage")
        return httpx.Response(
            status_code=200,
            json={
                "ok": True,
                "result": {
                    "message_id": 10,
                    "chat": {"id": 1, "type": "private"},
                    "date": 1,
                    "text": "hello",
                },
            },
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
    )

    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")
    response = client.post(
        "/internal/telegram/sendMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["result"]["text"] == "hello"

    transport_client._transport.close()


def test_send_message_returns_mocked_telegram_api_error(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "ok": False,
                "description": "Bad Request: chat not found",
                "error_code": 400,
            },
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
    )

    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")
    response = client.post(
        "/internal/telegram/sendMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "ok": False,
        "error_type": "telegram_api_error",
        "message": "telegram returned an application error",
        "status_code": 502,
        "telegram_status_code": None,
        "telegram_error_code": 400,
        "telegram_description": "Bad Request: chat not found",
        "telegram_response": {
            "ok": False,
            "description": "Bad Request: chat not found",
            "error_code": 400,
        },
        "details": None,
    }

    transport_client._transport.close()


def test_send_message_handles_timeout(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
    )

    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")
    response = client.post(
        "/internal/telegram/sendMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 504
    assert response.json() == {
        "ok": False,
        "error_type": "relay_timeout",
        "message": "telegram request timed out",
        "status_code": 504,
        "telegram_status_code": None,
        "telegram_error_code": None,
        "telegram_description": None,
        "telegram_response": None,
        "details": None,
    }

    transport_client._transport.close()
