"""Example-only FastAPI receiver for forwarded Telegram updates.

This file shows how a private backend can:
- receive forwarded Telegram updates from telegram-edge-relay
- verify the relay signature
- parse the forwarded update JSON

Production guidance:
- keep the shared secret outside source control
- add your application's request logging and tracing
- verify request size limits at the proxy and app layers
- verify the `Content-Type` and route this into your existing bot/application layer
- route the validated update into your own bot/application logic
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel

RELAY_TIMESTAMP_HEADER = "X-Relay-Timestamp"
RELAY_SIGNATURE_HEADER = "X-Relay-Signature"
INTERNAL_SHARED_SECRET = "replace-me"
SIGNATURE_TTL_SECONDS = 300

app = FastAPI()


class ForwardedTelegramUpdateAck(BaseModel):
    ok: bool
    request_id: str | None
    update_id: int | None


def build_signature(secret: str, body: bytes, timestamp: str) -> str:
    payload = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(*, secret: str, body: bytes, timestamp: str, signature: str) -> None:
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="missing relay auth headers")

    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid relay timestamp") from exc

    if abs(int(time.time()) - timestamp_value) > SIGNATURE_TTL_SECONDS:
        raise HTTPException(status_code=401, detail="stale relay signature")

    expected = build_signature(secret=secret, body=body, timestamp=timestamp)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid relay signature")


@app.post(
    "/internal/inbound/telegram-update",
    response_model=ForwardedTelegramUpdateAck,
)
async def receive_forwarded_update(request: Request) -> ForwardedTelegramUpdateAck:
    raw_body = await request.body()
    verify_signature(
        secret=INTERNAL_SHARED_SECRET,
        body=raw_body,
        timestamp=request.headers.get(RELAY_TIMESTAMP_HEADER, ""),
        signature=request.headers.get(RELAY_SIGNATURE_HEADER, ""),
    )

    try:
        payload: Any = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="telegram update must be a JSON object",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="telegram update must be a JSON object",
        )

    request_id = request.headers.get("X-Request-ID")
    update_id = payload.get("update_id")

    # Example-only handoff point.
    # Replace this block with your own application logic.
    return ForwardedTelegramUpdateAck(
        ok=True,
        request_id=request_id,
        update_id=update_id if isinstance(update_id, int) else None,
    )
