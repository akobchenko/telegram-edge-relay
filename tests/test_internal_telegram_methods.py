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


def install_mock_telegram_client(
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


def send_signed_multipart(
    client: TestClient,
    *,
    secret: str,
    path: str,
    data: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
) -> httpx.Response:
    request = client.build_request(
        "POST",
        path,
        data=data,
        files=files,
    )
    body = request.content
    headers = signed_headers(secret, body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]
    return client.send(request)


def test_send_photo_uses_multipart_and_succeeds(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendPhoto")
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'filename="photo.jpg"' in request.content
        assert b'image-bytes' in request.content
        assert (
            b'{"inline_keyboard":[[{"text":"Open","callback_data":"open"}]]}'
            in request.content
        )
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 11}},
        )

    transport_client = install_mock_telegram_client(
        client,
        httpx.MockTransport(handler),
    )
    response = send_signed_multipart(
        client=client,
        secret="test-shared-secret",
        path="/internal/telegram/sendPhoto",
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

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 11}}

    transport_client._transport.close()


def test_send_photo_rejects_missing_file(client: TestClient) -> None:
    request = client.build_request(
        "POST",
        "/internal/telegram/sendPhoto",
        data={"chat_id": "1", "caption": "hello"},
    )
    body = request.content
    headers = signed_headers("test-shared-secret", body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

    response = client.send(request)

    assert response.status_code == 422
    assert response.json()["error_type"] == "validation_error"


def test_send_photo_rejects_invalid_auth(client: TestClient) -> None:
    response = send_signed_multipart(
        client=client,
        secret="wrong-shared-secret",
        path="/internal/telegram/sendPhoto",
        data={"chat_id": "1"},
        files={"photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
    )

    assert response.status_code == 401
    assert response.json()["error_type"] == "auth_error"


def test_edit_message_text_rejects_invalid_body(client: TestClient) -> None:
    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")
    response = client.post(
        "/internal/telegram/editMessageText",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 422


def test_answer_callback_query_returns_api_error(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={
                "ok": False,
                "description": "Bad Request: query is too old",
                "error_code": 400,
            },
        )

    transport_client = install_mock_telegram_client(
        client,
        httpx.MockTransport(handler),
    )

    body = json.dumps({"callback_query_id": "cbq-1"}).encode("utf-8")
    response = client.post(
        "/internal/telegram/answerCallbackQuery",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 400
    assert response.json()["error_type"] == "telegram_http_error"

    transport_client._transport.close()


def test_delete_message_succeeds(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/deleteMessage")
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": True},
        )

    transport_client = install_mock_telegram_client(
        client,
        httpx.MockTransport(handler),
    )

    body = json.dumps({"chat_id": 1, "message_id": 7}).encode("utf-8")
    response = client.post(
        "/internal/telegram/deleteMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": True}

    transport_client._transport.close()


def test_send_chat_action_handles_timeout(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport_client = install_mock_telegram_client(
        client,
        httpx.MockTransport(handler),
    )

    body = json.dumps({"chat_id": 1, "action": "typing"}).encode("utf-8")
    response = client.post(
        "/internal/telegram/sendChatAction",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 504
    assert response.json()["error_type"] == "relay_timeout"

    transport_client._transport.close()


def test_edit_message_media_multipart_preserves_fields(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/editMessageMedia")
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert (
            b'{"type":"photo","media":"attach://new-photo","caption":"updated"}'
            in request.content
        )
        assert (
            b'{"inline_keyboard":[[{"text":"Refresh","callback_data":"refresh"}]]}'
            in request.content
        )
        assert b'filename="photo.jpg"' in request.content
        assert b"image-bytes" in request.content
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 21}},
        )

    transport_client = install_mock_telegram_client(
        client,
        httpx.MockTransport(handler),
    )
    response = send_signed_multipart(
        client=client,
        secret="test-shared-secret",
        path="/internal/telegram/editMessageMedia",
        data={
            "chat_id": "1",
            "message_id": "21",
            "media": json.dumps(
                {"type": "photo", "media": "attach://new-photo", "caption": "updated"},
                separators=(",", ":"),
            ),
            "reply_markup": json.dumps(
                {"inline_keyboard": [[{"text": "Refresh", "callback_data": "refresh"}]]},
                separators=(",", ":"),
            ),
        },
        files={"new-photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 21}}

    transport_client._transport.close()
