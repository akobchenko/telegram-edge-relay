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
from app.services import internal_telegram
from app.services.telegram_client import TelegramClient


def build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outbound_mode: str | None = None,
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
    monkeypatch.delenv("TELEGRAM_OUTBOUND_MODE", raising=False)
    if outbound_mode is not None:
        monkeypatch.setenv("TELEGRAM_OUTBOUND_MODE", outbound_mode)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with build_client(monkeypatch) as test_client:
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


def test_default_outbound_mode_is_mixed(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.delenv("TELEGRAM_OUTBOUND_MODE", raising=False)

    get_settings.cache_clear()
    assert get_settings().telegram_outbound_mode == "mixed"
    get_settings.cache_clear()


def test_raw_endpoint_rejects_invalid_signature(client: TestClient) -> None:
    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")

    response = client.post(
        "/internal/telegram/raw/sendMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("wrong-secret", body),
        },
    )

    assert response.status_code == 401
    assert response.json()["error_type"] == "auth_error"


def test_raw_endpoint_rejects_invalid_method_name(client: TestClient) -> None:
    body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")

    response = client.post(
        "/internal/telegram/raw/send.Message",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 422
    assert response.json()["error_type"] == "validation_error"


def test_raw_endpoint_is_rejected_in_typed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with build_client(monkeypatch, outbound_mode="typed") as client:
        body = json.dumps({"chat_id": 1, "text": "hello"}).encode("utf-8")
        response = client.post(
            "/internal/telegram/raw/sendMessage",
            content=body,
            headers={
                "content-type": "application/json",
                **signed_headers("test-shared-secret", body),
            },
        )

    assert response.status_code == 403
    assert response.json() == {
        "ok": False,
        "error_type": "operation_not_allowed",
        "message": "raw telegram fallback is disabled in typed mode",
        "status_code": 403,
        "telegram_status_code": None,
        "telegram_error_code": None,
        "telegram_description": None,
        "telegram_response": None,
        "telegram_response_text": None,
        "details": None,
    }


def test_typed_mode_allows_typed_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with build_client(monkeypatch, outbound_mode="typed") as client:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/sendMessage")
            return httpx.Response(
                status_code=200,
                json={"ok": True, "result": {"message_id": 12}},
            )

        transport_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.telegram.org",
        )
        client.app.state.telegram_client = TelegramClient(
            http_client=transport_client,
            bot_token="123456:test-token",
            outbound_mode="typed",
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
        transport_client._transport.close()

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 12}}


def test_raw_endpoint_returns_mocked_success(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendDice")
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 10}},
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    body = json.dumps({"chat_id": 1}).encode("utf-8")
    response = client.post(
        "/internal/telegram/raw/sendDice",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 10}}
    transport_client._transport.close()


def test_raw_endpoint_maps_telegram_api_error(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "ok": False,
                "description": "Bad Request: unsupported",
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
        outbound_mode="mixed",
    )

    body = json.dumps({"chat_id": 1}).encode("utf-8")
    response = client.post(
        "/internal/telegram/raw/sendDice",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 502
    assert response.json()["error_type"] == "telegram_api_error"
    transport_client._transport.close()


def test_raw_multipart_endpoint_preserves_fields_and_files(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendDocument")
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'filename="doc.txt"' in request.content
        assert b"hello document" in request.content
        assert (
            b'{"inline_keyboard":[[{"text":"Open","callback_data":"open"}]]}'
            in request.content
        )
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 18}},
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    request = client.build_request(
        "POST",
        "/internal/telegram/raw/sendDocument",
        data={
            "chat_id": "1",
            "caption": "hello",
            "reply_markup": json.dumps(
                {"inline_keyboard": [[{"text": "Open", "callback_data": "open"}]]},
                separators=(",", ":"),
            ),
        },
        files={"document": ("doc.txt", b"hello document", "text/plain")},
    )
    body = request.content
    headers = signed_headers("test-shared-secret", body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

    response = client.send(request)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 18}}
    transport_client._transport.close()


def test_raw_send_photo_multipart_succeeds(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendPhoto")
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'filename="photo.jpg"' in request.content
        assert b"image-bytes" in request.content
        assert b'name="chat_id"' in request.content
        assert b'name="caption"' in request.content
        assert (
            b'{"inline_keyboard":[[{"text":"Open","callback_data":"open"}]]}'
            in request.content
        )
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 19}},
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    request = client.build_request(
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

    response = client.send(request)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 19}}
    transport_client._transport.close()


def test_raw_edit_message_media_multipart_preserves_media_field(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/editMessageMedia")
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert (
            b'{"type":"photo","media":"attach://new-photo","caption":"updated"}'
            in request.content
        )
        assert b'filename="photo.jpg"' in request.content
        assert b"image-bytes" in request.content
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": 27}},
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    request = client.build_request(
        "POST",
        "/internal/telegram/raw/editMessageMedia",
        data={
            "chat_id": "1",
            "message_id": "27",
            "media": json.dumps(
                {"type": "photo", "media": "attach://new-photo", "caption": "updated"},
                separators=(",", ":"),
            ),
        },
        files={"new-photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
    )
    body = request.content
    headers = signed_headers("test-shared-secret", body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

    response = client.send(request)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 27}}
    transport_client._transport.close()


def test_raw_multipart_parse_failure_returns_json_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_parse_form_data(request):  # type: ignore[no-untyped-def]
        raise internal_telegram.MultipartForwardError("failed to parse form data")

    monkeypatch.setattr(internal_telegram, "parse_form_data", broken_parse_form_data)

    request = client.build_request(
        "POST",
        "/internal/telegram/raw/sendPhoto",
        data={"chat_id": "1"},
        files={"photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
    )
    body = request.content
    headers = signed_headers("test-shared-secret", body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

    response = client.send(request)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "ok": False,
        "error_type": "validation_error",
        "message": "failed to parse form data",
        "status_code": 422,
        "telegram_status_code": None,
        "telegram_error_code": None,
        "telegram_description": None,
        "telegram_response": None,
        "telegram_response_text": None,
        "details": None,
    }


def test_raw_multipart_build_failure_returns_json_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": True})

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    def broken_build_multipart_body(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("Attempted to send an sync request with an AsyncClient instance.")

    monkeypatch.setattr(
        client.app.state.telegram_client,
        "_build_multipart_body",
        broken_build_multipart_body,
    )

    request = client.build_request(
        "POST",
        "/internal/telegram/raw/sendPhoto",
        data={"chat_id": "1"},
        files={"photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
    )
    body = request.content
    headers = signed_headers("test-shared-secret", body)
    request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
    request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

    response = client.send(request)

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "ok": False,
        "error_type": "relay_network_error",
        "message": "telegram transport error",
        "status_code": 502,
        "telegram_status_code": None,
        "telegram_error_code": None,
        "telegram_description": None,
        "telegram_response": None,
        "telegram_response_text": "Attempted to send an sync request with an AsyncClient instance.",
        "details": None,
    }
    transport_client._transport.close()


def test_raw_http_error_preserves_upstream_text(client: TestClient) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, content=b"upstream unavailable")

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    body = json.dumps({"chat_id": 1}).encode("utf-8")
    response = client.post(
        "/internal/telegram/raw/sendDice",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 500
    assert response.json()["error_type"] == "telegram_http_error"
    assert response.json()["telegram_response_text"] == "upstream unavailable"
    transport_client._transport.close()


def test_raw_endpoint_rejects_non_object_body(client: TestClient) -> None:
    body = json.dumps([{"chat_id": 1}]).encode("utf-8")

    response = client.post(
        "/internal/telegram/raw/sendMessage",
        content=body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", body),
        },
    )

    assert response.status_code == 422
    assert response.json()["error_type"] == "validation_error"


def test_typed_and_raw_send_message_forward_equivalent_payloads(client: TestClient) -> None:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": len(captured)}},
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    typed_body = json.dumps(
        {
            "chat_id": 1,
            "text": "hello",
            "reply_markup": {
                "inline_keyboard": [[{"text": "Open", "callback_data": "open"}]]
            },
        }
    ).encode("utf-8")
    raw_body = json.dumps(
        {
            "chat_id": 1,
            "text": "hello",
            "reply_markup": {
                "inline_keyboard": [[{"text": "Open", "callback_data": "open"}]]
            },
        }
    ).encode("utf-8")

    typed_response = client.post(
        "/internal/telegram/sendMessage",
        content=typed_body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", typed_body),
        },
    )
    raw_response = client.post(
        "/internal/telegram/raw/sendMessage",
        content=raw_body,
        headers={
            "content-type": "application/json",
            **signed_headers("test-shared-secret", raw_body),
        },
    )

    assert typed_response.status_code == 200
    assert raw_response.status_code == 200
    assert captured[0] == captured[1]
    transport_client._transport.close()


def test_typed_and_raw_send_photo_succeed_for_equivalent_payloads(client: TestClient) -> None:
    captured: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        return httpx.Response(
            status_code=200,
            json={"ok": True, "result": {"message_id": len(captured)}},
        )

    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    client.app.state.telegram_client = TelegramClient(
        http_client=transport_client,
        bot_token="123456:test-token",
        outbound_mode="mixed",
    )

    typed_request = client.build_request(
        "POST",
        "/internal/telegram/sendPhoto",
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
    typed_body = typed_request.content
    typed_headers = signed_headers("test-shared-secret", typed_body)
    typed_request.headers[INTERNAL_TIMESTAMP_HEADER] = typed_headers[INTERNAL_TIMESTAMP_HEADER]
    typed_request.headers[INTERNAL_SIGNATURE_HEADER] = typed_headers[INTERNAL_SIGNATURE_HEADER]

    raw_request = client.build_request(
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
    raw_body = raw_request.content
    raw_headers = signed_headers("test-shared-secret", raw_body)
    raw_request.headers[INTERNAL_TIMESTAMP_HEADER] = raw_headers[INTERNAL_TIMESTAMP_HEADER]
    raw_request.headers[INTERNAL_SIGNATURE_HEADER] = raw_headers[INTERNAL_SIGNATURE_HEADER]

    typed_response = client.send(typed_request)
    raw_response = client.send(raw_request)

    assert typed_response.status_code == 200
    assert raw_response.status_code == 200
    for expected in (
        b'filename="photo.jpg"',
        b"image-bytes",
        b'name="chat_id"',
        b'name="caption"',
        b'{"inline_keyboard":[[{"text":"Open","callback_data":"open"}]]}',
    ):
        assert expected in captured[0]
        assert expected in captured[1]
    transport_client._transport.close()


def test_proxy_mode_preserves_normalized_error_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with build_client(monkeypatch, outbound_mode="proxy") as client:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/sendMessage")
            raise httpx.ReadTimeout("timed out", request=request)

        transport_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.telegram.org",
        )
        client.app.state.telegram_client = TelegramClient(
            http_client=transport_client,
            bot_token="123456:test-token",
            outbound_mode="proxy",
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
        transport_client._transport.close()

    assert response.status_code == 504
    assert response.json()["error_type"] == "relay_timeout"


def test_proxy_mode_keeps_send_photo_on_typed_multipart_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with build_client(monkeypatch, outbound_mode="proxy") as client:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/sendPhoto")
            assert request.headers["content-type"].startswith("multipart/form-data")
            return httpx.Response(
                status_code=200,
                json={"ok": True, "result": {"message_id": 33}},
            )

        transport_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.telegram.org",
        )
        client.app.state.telegram_client = TelegramClient(
            http_client=transport_client,
            bot_token="123456:test-token",
            outbound_mode="proxy",
        )

        request = client.build_request(
            "POST",
            "/internal/telegram/sendPhoto",
            data={"chat_id": "1", "caption": "hello"},
            files={"photo": ("photo.jpg", b"image-bytes", "image/jpeg")},
        )
        body = request.content
        headers = signed_headers("test-shared-secret", body)
        request.headers[INTERNAL_TIMESTAMP_HEADER] = headers[INTERNAL_TIMESTAMP_HEADER]
        request.headers[INTERNAL_SIGNATURE_HEADER] = headers[INTERNAL_SIGNATURE_HEADER]

        response = client.send(request)
        transport_client._transport.close()

    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": {"message_id": 33}}
