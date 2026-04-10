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
    outbound_mode: str = "mixed",
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
    monkeypatch.setenv("TELEGRAM_OUTBOUND_MODE", outbound_mode)
    monkeypatch.setenv("BACKEND_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("TELEGRAM_RESPONSE_MODE", "transparent")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with build_client(monkeypatch) as test_client:
        yield test_client
    get_settings.cache_clear()


def signed_headers(body: bytes, timestamp: int | None = None) -> dict[str, str]:
    current_timestamp = str(timestamp or int(time.time()))
    return {
        INTERNAL_TIMESTAMP_HEADER: current_timestamp,
        INTERNAL_SIGNATURE_HEADER: build_internal_signature(
            "test-shared-secret",
            current_timestamp,
            body,
        ),
    }


def install_mock_telegram_client(
    client: TestClient,
    handler: httpx.MockTransport,
    *,
    outbound_mode: str = "mixed",
) -> httpx.AsyncClient:
    transport_client = httpx.AsyncClient(
        transport=handler,
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode=outbound_mode,  # type: ignore[arg-type]
    )
    return transport_client


def test_canonical_unknown_method_forwards_json_payload(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getFile")
        assert json.loads(request.content.decode("utf-8")) == {"file_id": "abc123"}
        return httpx.Response(
            status_code=200,
            json={
                "ok": True,
                "result": {
                    "file_id": "abc123",
                    "file_path": "photos/file_1.jpg",
                },
            },
        )

    transport_client = install_mock_telegram_client(client, httpx.MockTransport(handler))
    body = json.dumps({"file_id": "abc123"}).encode("utf-8")
    response = client.post(
        "/internal/telegram/getFile",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers(body),
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "result": {
            "file_id": "abc123",
            "file_path": "photos/file_1.jpg",
        },
    }
    transport_client._transport.close()


def test_canonical_unknown_method_is_rejected_in_typed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with build_client(monkeypatch, outbound_mode="typed") as client:
        body = json.dumps({"file_id": "abc123"}).encode("utf-8")
        response = client.post(
            "/internal/telegram/getFile",
            content=body,
            headers={
                "content-type": "application/json",
                **signed_headers(body),
            },
        )

    assert response.status_code == 403
    assert response.json()["error_type"] == "operation_not_allowed"


def test_internal_file_download_proxies_binary_content(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/file/bot123456:test-token/photos/file_1.jpg"
        return httpx.Response(
            status_code=200,
            content=b"image-bytes",
            headers={
                "content-type": "image/jpeg",
                "content-length": "11",
            },
        )

    transport_client = install_mock_telegram_client(client, httpx.MockTransport(handler))
    response = client.get(
        "/internal/telegram/file/photos/file_1.jpg",
        headers=signed_headers(b""),
    )

    assert response.status_code == 200
    assert response.content == b"image-bytes"
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["content-length"] == "11"
    transport_client._transport.close()


def test_internal_file_download_rejects_invalid_path(client: TestClient) -> None:
    response = client.get(
        "/internal/telegram/file/%2E%2E/secret",
        headers=signed_headers(b""),
    )

    assert response.status_code == 422
    assert response.json()["error_type"] == "validation_error"


def test_internal_file_download_maps_upstream_http_error(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=404,
            content=b"Not Found",
            headers={"content-type": "text/plain"},
        )

    transport_client = install_mock_telegram_client(client, httpx.MockTransport(handler))
    response = client.get(
        "/internal/telegram/file/photos/file_404.jpg",
        headers=signed_headers(b""),
    )

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "description": "telegram file download failed",
        "error_type": "telegram_http_error",
        "raw_response_text": "Not Found",
    }
    transport_client._transport.close()
