from __future__ import annotations

import pytest

from app.security.signing import (
    INTERNAL_SIGNATURE_HEADER,
    INTERNAL_TIMESTAMP_HEADER,
    SignatureVerificationError,
    build_signature_headers,
    sign_payload,
    verify_signature,
)


def test_verify_signature_accepts_valid_signature() -> None:
    body = b'{"event":"telegram_update"}'
    timestamp = "1700000000"
    signature = sign_payload("shared-secret", body, timestamp)

    verify_signature(
        secret="shared-secret",
        timestamp=timestamp,
        signature=signature,
        body=body,
        ttl_seconds=300,
        now=1700000001,
    )


def test_verify_signature_rejects_invalid_signature() -> None:
    with pytest.raises(SignatureVerificationError, match="invalid signature"):
        verify_signature(
            secret="shared-secret",
            timestamp="1700000000",
            signature="sha256=" + ("0" * 64),
            body=b"payload",
            ttl_seconds=300,
            now=1700000001,
        )


def test_verify_signature_rejects_invalid_format() -> None:
    with pytest.raises(SignatureVerificationError, match="invalid signature format"):
        verify_signature(
            secret="shared-secret",
            timestamp="1700000000",
            signature="bad",
            body=b"payload",
            ttl_seconds=300,
            now=1700000001,
        )


def test_verify_signature_rejects_stale_timestamp() -> None:
    body = b"payload"
    timestamp = "1700000000"
    signature = sign_payload("shared-secret", body, timestamp)

    with pytest.raises(SignatureVerificationError, match="stale signature timestamp"):
        verify_signature(
            secret="shared-secret",
            timestamp=timestamp,
            signature=signature,
            body=body,
            ttl_seconds=300,
            now=1700000400,
        )


def test_verify_signature_rejects_modified_payload() -> None:
    original_body = b'{"message":"hello"}'
    modified_body = b'{"message":"tampered"}'
    timestamp = "1700000000"
    signature = sign_payload("shared-secret", original_body, timestamp)

    with pytest.raises(SignatureVerificationError, match="invalid signature"):
        verify_signature(
            secret="shared-secret",
            timestamp=timestamp,
            signature=signature,
            body=modified_body,
            ttl_seconds=300,
            now=1700000001,
        )


def test_verify_signature_rejects_missing_headers() -> None:
    with pytest.raises(SignatureVerificationError, match="missing signature headers"):
        verify_signature(
            secret="shared-secret",
            timestamp="",
            signature="",
            body=b"payload",
            ttl_seconds=300,
            now=1700000001,
        )


def test_build_signature_headers_returns_expected_header_names() -> None:
    headers = build_signature_headers(
        secret="shared-secret",
        body=b"payload",
        timestamp=1700000000,
    )

    assert headers[INTERNAL_TIMESTAMP_HEADER] == "1700000000"
    assert headers[INTERNAL_SIGNATURE_HEADER].startswith("sha256=")
