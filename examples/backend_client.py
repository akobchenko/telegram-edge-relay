"""Example-only relay client for a private backend.

This file shows how to:
- sign internal requests to telegram-edge-relay
- call /internal/telegram/sendMessage
- call /internal/telegram/sendPhoto

Production guidance:
- load secrets from real secret storage
- set explicit retry policy only where safe
- add application-level logging and metrics
- handle multipart body generation and signing in one place
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
import httpx

RELAY_TIMESTAMP_HEADER = "X-Relay-Timestamp"
RELAY_SIGNATURE_HEADER = "X-Relay-Signature"


def build_signature(secret: str, body: bytes, timestamp: str) -> str:
    payload = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@dataclass(frozen=True)
class RelayClientConfig:
    relay_base_url: str
    internal_shared_secret: str
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class SendMessageRequest:
    chat_id: int | str
    text: str


@dataclass(frozen=True)
class SendPhotoRequest:
    chat_id: int | str
    photo_path: str
    caption: str | None = None


class RelayClient:
    def __init__(self, config: RelayClientConfig) -> None:
        self._config = config
        self._http_client = httpx.Client(
            base_url=config.relay_base_url.rstrip("/"),
            timeout=httpx.Timeout(config.timeout_seconds),
        )

    def close(self) -> None:
        self._http_client.close()

    def send_message(self, payload: SendMessageRequest) -> httpx.Response:
        body = json.dumps(
            {
                "chat_id": payload.chat_id,
                "text": payload.text,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = self._build_signed_headers(body, content_type="application/json")
        return self._http_client.post(
            "/internal/telegram/sendMessage",
            content=body,
            headers=headers,
        )

    def send_photo(
        self,
        payload: SendPhotoRequest,
    ) -> httpx.Response:
        photo_bytes = Path(payload.photo_path).read_bytes()
        filename = Path(payload.photo_path).name

        # Example-only multipart builder.
        # In production, centralize this so signing and sending always use the exact same bytes.
        boundary = f"relay-example-{int(time.time() * 1000)}"
        body = self._encode_multipart(
            boundary=boundary,
            fields={
                "chat_id": str(payload.chat_id),
                **({"caption": payload.caption} if payload.caption is not None else {}),
            },
            file_field_name="photo",
            filename=filename,
            file_content=photo_bytes,
            content_type="application/octet-stream",
        )
        headers = self._build_signed_headers(
            body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return self._http_client.post(
            "/internal/telegram/sendPhoto",
            content=body,
            headers=headers,
        )

    def _build_signed_headers(self, body: bytes, *, content_type: str) -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = build_signature(
            self._config.internal_shared_secret,
            body,
            timestamp,
        )
        return {
            "Content-Type": content_type,
            RELAY_TIMESTAMP_HEADER: timestamp,
            RELAY_SIGNATURE_HEADER: signature,
        }

    @staticmethod
    def _encode_multipart(
        *,
        boundary: str,
        fields: dict[str, str],
        file_field_name: str,
        filename: str,
        file_content: bytes,
        content_type: str,
    ) -> bytes:
        chunks: list[bytes] = []
        for key, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{file_field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                file_content,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        return b"".join(chunks)


if __name__ == "__main__":
    config = RelayClientConfig(
        relay_base_url="https://relay.example.com",
        internal_shared_secret="replace-me",
    )
    client = RelayClient(config)
    try:
        send_message_response = client.send_message(
            SendMessageRequest(
                chat_id=123456,
                text="hello from the private backend",
            )
        )
        print("sendMessage:", send_message_response.status_code, send_message_response.text)

        send_photo_response = client.send_photo(
            SendPhotoRequest(
                chat_id=123456,
                photo_path="example.jpg",
                caption="uploaded via relay",
            )
        )
        print("sendPhoto:", send_photo_response.status_code, send_photo_response.text)
    finally:
        client.close()
