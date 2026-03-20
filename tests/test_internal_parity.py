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


def build_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    monkeypatch.setenv("TELEGRAM_RESPONSE_MODE", "transparent")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with build_client(monkeypatch) as test_client:
        yield test_client
    get_settings.cache_clear()


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


def signed_headers(body: bytes, timestamp: int | None = None) -> dict[str, str]:
    current_timestamp = str(timestamp or int(time.time()))
    return {
        INTERNAL_TIMESTAMP_HEADER: current_timestamp,
        INTERNAL_SIGNATURE_HEADER: build_internal_signature(
            secret="test-shared-secret",
            timestamp=current_timestamp,
            body=body,
        ),
    }


def post_signed_json(
    client: TestClient,
    *,
    path: str,
    payload: dict[str, object],
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return client.post(
        path,
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers(body),
        },
    )


def send_signed_multipart(
    client: TestClient,
    *,
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
    headers = signed_headers(body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]
    return client.send(request)


@pytest.mark.parametrize(
    ("method_name", "typed_path", "payload"),
    [
        (
            "sendMessage",
            "/internal/telegram/sendMessage",
            {
                "chat_id": 1,
                "text": "hello",
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Open", "callback_data": "open"}]]
                },
            },
        ),
        (
            "editMessageText",
            "/internal/telegram/editMessageText",
            {
                "chat_id": 1,
                "message_id": 7,
                "text": "updated",
                "disable_web_page_preview": True,
            },
        ),
        (
            "editMessageCaption",
            "/internal/telegram/editMessageCaption",
            {
                "chat_id": 1,
                "message_id": 7,
                "caption": "updated",
            },
        ),
        (
            "answerCallbackQuery",
            "/internal/telegram/answerCallbackQuery",
            {
                "callback_query_id": "cbq-1",
                "text": "done",
                "show_alert": False,
            },
        ),
        (
            "sendChatAction",
            "/internal/telegram/sendChatAction",
            {
                "chat_id": 1,
                "action": "typing",
            },
        ),
    ],
)
def test_typed_and_raw_json_paths_are_transport_equivalent(
    client: TestClient,
    method_name: str,
    typed_path: str,
    payload: dict[str, object],
) -> None:
    captured: list[tuple[str, str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (
                request.url.path,
                request.headers.get("content-type", ""),
                request.content,
            )
        )
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 42}},
        )

    transport_client = install_mock_client(client, httpx.MockTransport(handler))

    typed_response = post_signed_json(client, path=typed_path, payload=payload)
    raw_response = post_signed_json(
        client,
        path=f"/internal/telegram/raw/{method_name}",
        payload=payload,
    )

    assert typed_response.status_code == 200
    assert raw_response.status_code == 200
    assert typed_response.json() == raw_response.json() == {
        "ok": True,
        "result": {"message_id": 42},
    }
    assert len(captured) == 2
    assert captured[0][0] == captured[1][0] == f"/bot123456:test-token/{method_name}"
    assert captured[0][1].startswith("application/json")
    assert captured[1][1].startswith("application/json")
    assert json.loads(captured[0][2].decode("utf-8")) == payload
    assert json.loads(captured[1][2].decode("utf-8")) == payload

    transport_client._transport.close()


def test_typed_and_raw_send_photo_are_transport_equivalent(
    client: TestClient,
) -> None:
    captured: list[tuple[str, str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (
                request.url.path,
                request.headers.get("content-type", ""),
                request.content,
            )
        )
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 77}},
        )

    transport_client = install_mock_client(client, httpx.MockTransport(handler))
    data = {
        "chat_id": "1",
        "caption": "hello",
        "reply_markup": json.dumps(
            {"inline_keyboard": [[{"text": "Open", "callback_data": "open"}]]},
            separators=(",", ":"),
        ),
    }
    files = {"photo": ("photo.jpg", b"image-bytes", "image/jpeg")}

    typed_response = send_signed_multipart(
        client,
        path="/internal/telegram/sendPhoto",
        data=data,
        files=files,
    )
    raw_response = send_signed_multipart(
        client,
        path="/internal/telegram/raw/sendPhoto",
        data=data,
        files=files,
    )

    assert typed_response.status_code == 200
    assert raw_response.status_code == 200
    assert typed_response.json() == raw_response.json() == {
        "ok": True,
        "result": {"message_id": 77},
    }
    assert len(captured) == 2
    assert captured[0][0] == captured[1][0] == "/bot123456:test-token/sendPhoto"
    assert captured[0][1].startswith("multipart/form-data")
    assert captured[1][1].startswith("multipart/form-data")
    for expected in (
        b'filename="photo.jpg"',
        b"image-bytes",
        b'name="chat_id"',
        b"name=\"caption\"",
        b'{"inline_keyboard":[[{"text":"Open","callback_data":"open"}]]}',
    ):
        assert expected in captured[0][2]
        assert expected in captured[1][2]

    transport_client._transport.close()


def test_typed_and_raw_edit_message_media_are_transport_equivalent(
    client: TestClient,
) -> None:
    captured: list[tuple[str, str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (
                request.url.path,
                request.headers.get("content-type", ""),
                request.content,
            )
        )
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 90}},
        )

    transport_client = install_mock_client(client, httpx.MockTransport(handler))
    data = {
        "chat_id": "1",
        "message_id": "27",
        "media": json.dumps(
            {"type": "photo", "media": "attach://new-photo", "caption": "updated"},
            separators=(",", ":"),
        ),
    }
    files = {"new-photo": ("photo.jpg", b"image-bytes", "image/jpeg")}

    typed_response = send_signed_multipart(
        client,
        path="/internal/telegram/editMessageMedia",
        data=data,
        files=files,
    )
    raw_response = send_signed_multipart(
        client,
        path="/internal/telegram/raw/editMessageMedia",
        data=data,
        files=files,
    )

    assert typed_response.status_code == 200
    assert raw_response.status_code == 200
    assert typed_response.json() == raw_response.json() == {
        "ok": True,
        "result": {"message_id": 90},
    }
    assert len(captured) == 2
    assert captured[0][0] == captured[1][0] == "/bot123456:test-token/editMessageMedia"
    assert captured[0][1].startswith("multipart/form-data")
    assert captured[1][1].startswith("multipart/form-data")
    for expected in (
        b'name="chat_id"',
        b'name="message_id"',
        b'{"type":"photo","media":"attach://new-photo","caption":"updated"}',
        b'filename="photo.jpg"',
        b"image-bytes",
    ):
        assert expected in captured[0][2]
        assert expected in captured[1][2]

    transport_client._transport.close()


def test_typed_and_raw_answer_callback_query_return_same_transparent_error(
    client: TestClient,
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

    transport_client = install_mock_client(client, httpx.MockTransport(handler))
    payload = {"callback_query_id": "stale"}

    typed_response = post_signed_json(
        client,
        path="/internal/telegram/answerCallbackQuery",
        payload=payload,
    )
    raw_response = post_signed_json(
        client,
        path="/internal/telegram/raw/answerCallbackQuery",
        payload=payload,
    )

    assert typed_response.status_code == 400
    assert raw_response.status_code == 400
    assert typed_response.json() == raw_response.json() == {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: query is too old",
    }

    transport_client._transport.close()
