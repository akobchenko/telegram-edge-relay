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


def build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response_mode: str,
) -> TestClient:
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
    monkeypatch.setenv("TELEGRAM_RESPONSE_MODE", response_mode)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def transparent_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with build_client(monkeypatch, response_mode="transparent") as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def normalized_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with build_client(monkeypatch, response_mode="normalized") as test_client:
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


def install_mock_client(
    client: TestClient,
    handler: httpx.MockTransport,
) -> httpx.AsyncClient:
    transport_client = httpx.AsyncClient(
        transport=handler,
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )
    return transport_client


def test_transparent_mode_send_message_success(
    transparent_client: TestClient,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 42, "text": "hello"}},
        )

    transport_client = install_mock_client(
        transparent_client,
        httpx.MockTransport(handler),
    )

    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")
    response = transparent_client.post(
        "/internal/telegram/sendMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 42, "text": "hello"}}
    transport_client._transport.close()


def test_transparent_mode_edit_message_caption_returns_telegram_400(
    transparent_client: TestClient,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message is not modified",
            },
        )

    transport_client = install_mock_client(
        transparent_client,
        httpx.MockTransport(handler),
    )

    body = json.dumps({"chat_id": 1, "message_id": 7, "caption": "hi"}).encode("utf-8")
    response = transparent_client.post(
        "/internal/telegram/editMessageCaption",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: message is not modified",
    }
    transport_client._transport.close()


def test_transparent_mode_answer_callback_query_returns_telegram_400(
    transparent_client: TestClient,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: query is too old",
            },
        )

    transport_client = install_mock_client(
        transparent_client,
        httpx.MockTransport(handler),
    )

    body = json.dumps({"callback_query_id": "stale"}).encode("utf-8")
    response = transparent_client.post(
        "/internal/telegram/answerCallbackQuery",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: query is too old",
    }
    transport_client._transport.close()


def test_transparent_mode_raw_multipart_send_photo_success(
    transparent_client: TestClient,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendPhoto")
        assert request.headers["content-type"].startswith("multipart/form-data")
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 10}},
        )

    transport_client = install_mock_client(
        transparent_client,
        httpx.MockTransport(handler),
    )

    request = transparent_client.build_request(
        "POST",
        "/internal/telegram/raw/sendPhoto",
        data={
            "chat_id": "1",
            "caption": "hello",
            "reply_markup": json.dumps(
                {"inline_keyboard": [[{"text": "Open", "callback_data": "open"}]]},
                separators=(",", ":"),
            ),
        },
        files={"photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
    )
    body = request.content
    headers = signed_headers("test-shared-secret", body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

    response = transparent_client.send(request)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 10}}
    transport_client._transport.close()


def test_transparent_mode_raw_multipart_telegram_error(
    transparent_client: TestClient,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
        )

    transport_client = install_mock_client(
        transparent_client,
        httpx.MockTransport(handler),
    )

    request = transparent_client.build_request(
        "POST",
        "/internal/telegram/raw/sendPhoto",
        data={"chat_id": "1"},
        files={"photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
    )
    body = request.content
    headers = signed_headers("test-shared-secret", body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

    response = transparent_client.send(request)

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: chat not found",
    }
    transport_client._transport.close()


def test_normalized_mode_regression_keeps_existing_error_envelope(
    normalized_client: TestClient,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: query is too old",
            },
        )

    transport_client = install_mock_client(
        normalized_client,
        httpx.MockTransport(handler),
    )

    body = json.dumps({"callback_query_id": "stale"}).encode("utf-8")
    response = normalized_client.post(
        "/internal/telegram/answerCallbackQuery",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 400
    assert response.json()["error_type"] == "telegram_http_error"
    assert response.json()["telegram_description"] == "Bad Request: query is too old"
    transport_client._transport.close()
