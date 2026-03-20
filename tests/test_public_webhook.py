from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.main import create_app
from app.security.signing import INTERNAL_SIGNATURE_HEADER, INTERNAL_TIMESTAMP_HEADER
from app.services.backend_forwarder import BackendForwardResult, BackendForwarder


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


def test_webhook_rejects_wrong_path_secret(client: TestClient) -> None:
    response = client.post(
        "/telegram/webhook/wrong-secret",
        json={"update_id": 1},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "invalid webhook secret"}


def test_webhook_rejects_invalid_json_shape(client: TestClient) -> None:
    response = client.post(
        "/telegram/webhook/webhook-secret-value",
        content=json.dumps([{"update_id": 1}]).encode("utf-8"),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "request body must be a JSON object"}


def test_webhook_forwards_exact_body_successfully(client: TestClient) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        captured["timestamp"] = request.headers[INTERNAL_TIMESTAMP_HEADER]
        captured["signature"] = request.headers[INTERNAL_SIGNATURE_HEADER]
        captured["request_id"] = request.headers[REQUEST_ID_HEADER]
        return httpx.Response(status_code=202)

    backend_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://backend.example",
    )
    client.app.state.backend_forwarder = BackendForwarder(
        http_client=backend_client,
        shared_secret="test-shared-secret",
        forward_path="/internal/inbound/telegram-update",
    )

    raw_body = b'{"update_id":1,"message":{"text":"hello"}}'
    response = client.post(
        "/telegram/webhook/webhook-secret-value",
        content=raw_body,
        headers={"content-type": "application/json", "x-request-id": "req-123"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured["path"] == "/internal/inbound/telegram-update"
    assert captured["body"] == raw_body
    assert captured["timestamp"]
    assert str(captured["signature"]).startswith("sha256=")
    assert captured["request_id"] == "req-123"

    backend_client._transport.close()


def test_webhook_generates_safe_request_id_when_header_is_invalid(client: TestClient) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request_id"] = request.headers[REQUEST_ID_HEADER]
        return httpx.Response(status_code=202)

    backend_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://backend.example",
    )
    client.app.state.backend_forwarder = BackendForwarder(
        http_client=backend_client,
        shared_secret="test-shared-secret",
        forward_path="/internal/inbound/telegram-update",
    )

    response = client.post(
        "/telegram/webhook/webhook-secret-value",
        json={"update_id": 1},
        headers={REQUEST_ID_HEADER: "bad value with spaces"},
    )

    assert response.status_code == 200
    assert captured["request_id"] != "bad value with spaces"
    assert " " not in str(captured["request_id"])

    backend_client._transport.close()


def test_webhook_returns_backend_failure(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, json={"detail": "failure"})

    backend_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://backend.example",
    )
    client.app.state.backend_forwarder = BackendForwarder(
        http_client=backend_client,
        shared_secret="test-shared-secret",
        forward_path="/internal/inbound/telegram-update",
    )

    response = client.post(
        "/telegram/webhook/webhook-secret-value",
        json={"update_id": 1},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "backend forward request failed"}

    backend_client._transport.close()


def test_webhook_returns_backend_timeout(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    backend_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://backend.example",
    )
    client.app.state.backend_forwarder = BackendForwarder(
        http_client=backend_client,
        shared_secret="test-shared-secret",
        forward_path="/internal/inbound/telegram-update",
    )

    response = client.post(
        "/telegram/webhook/webhook-secret-value",
        json={"update_id": 1},
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "backend forward request timed out"}

    backend_client._transport.close()


def test_backend_forwarder_returns_invalid_response_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, content=b"not-json")

    backend_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://backend.example",
    )
    forwarder = BackendForwarder(
        http_client=backend_client,
        shared_secret="test-shared-secret",
        forward_path="/internal/inbound/telegram-update",
    )

    result = asyncio.run(forwarder.forward_telegram_update(b'{"update_id":1}'))

    assert result == BackendForwardResult(
        ok=False,
        status_code=502,
        error_type="invalid_response",
        description="backend forward response was invalid",
    )

    backend_client._transport.close()
