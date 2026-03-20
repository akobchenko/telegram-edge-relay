from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.logging import get_logger

INTERNAL_TIMESTAMP_HEADER = "X-Relay-Timestamp"
INTERNAL_SIGNATURE_HEADER = "X-Relay-Signature"
SIGNATURE_PREFIX = "sha256="
_HTTP_ERROR_DETAILS = {
    "missing signature headers": "missing internal auth headers",
    "invalid signature timestamp": "invalid internal timestamp",
    "stale signature timestamp": "stale internal signature",
    "invalid signature format": "invalid internal signature",
    "invalid signature": "invalid internal signature",
}


@dataclass(frozen=True)
class SignatureVerificationError(Exception):
    detail: str


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    payload = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def build_signature_headers(
    *,
    secret: str,
    body: bytes,
    timestamp: int | str | None = None,
) -> dict[str, str]:
    timestamp_value = str(int(time.time()) if timestamp is None else timestamp)
    return {
        INTERNAL_TIMESTAMP_HEADER: timestamp_value,
        INTERNAL_SIGNATURE_HEADER: sign_payload(
            secret=secret,
            body=body,
            timestamp=timestamp_value,
        ),
    }


def verify_signature(
    *,
    secret: str,
    timestamp: str,
    signature: str,
    body: bytes,
    ttl_seconds: int,
    now: int | None = None,
) -> None:
    if not timestamp or not signature:
        raise SignatureVerificationError("missing signature headers")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise SignatureVerificationError("invalid signature timestamp") from exc

    current_time = now if now is not None else int(time.time())
    if abs(current_time - timestamp_value) > ttl_seconds:
        raise SignatureVerificationError("stale signature timestamp")

    if not signature.startswith(SIGNATURE_PREFIX) or len(signature) != len(SIGNATURE_PREFIX) + 64:
        raise SignatureVerificationError("invalid signature format")

    expected_signature = sign_payload(secret=secret, body=body, timestamp=timestamp)
    if not hmac.compare_digest(expected_signature, signature):
        raise SignatureVerificationError("invalid signature")


def build_internal_signature(secret: str, timestamp: str, body: bytes) -> str:
    return sign_payload(secret=secret, body=body, timestamp=timestamp)


def verify_internal_signature(
    *,
    secret: str,
    timestamp: str,
    signature: str,
    body: bytes,
    max_age_seconds: int,
    now: int | None = None,
) -> None:
    verify_signature(
        secret=secret,
        timestamp=timestamp,
        signature=signature,
        body=body,
        ttl_seconds=max_age_seconds,
        now=now,
    )


async def require_internal_signature(request: Request) -> None:
    settings = get_settings()
    logger = get_logger("app.internal.auth")
    body = await request.body()
    try:
        verify_signature(
            secret=settings.internal_shared_secret.get_secret_value(),
            timestamp=request.headers.get(INTERNAL_TIMESTAMP_HEADER, ""),
            signature=request.headers.get(INTERNAL_SIGNATURE_HEADER, ""),
            body=body,
            ttl_seconds=settings.signature_ttl_seconds,
        )
    except SignatureVerificationError as exc:
        logger.warning(
            "internal_auth_failed",
            extra={
                "direction": "telegram_outbound",
                "route": request.url.path,
                "outcome": "auth_failed",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_HTTP_ERROR_DETAILS.get(exc.detail, "invalid internal signature"),
        ) from exc
