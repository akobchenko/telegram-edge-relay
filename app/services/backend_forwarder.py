from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.request_id import REQUEST_ID_HEADER, get_request_id
from app.logging import build_log_extra, get_logger
from app.security.signing import build_signature_headers

logger = get_logger("app.backend.forwarder")


@dataclass(frozen=True)
class BackendForwardResult:
    ok: bool
    status_code: int | None
    error_type: Literal["timeout", "network_error", "non_2xx", "invalid_response"] | None
    description: str | None = None


class BackendForwarder:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        shared_secret: str,
        forward_path: str,
    ) -> None:
        self._http_client = http_client
        self._shared_secret = shared_secret
        self._forward_path = forward_path

    async def forward_telegram_update(self, body: bytes) -> BackendForwardResult:
        route = "/telegram/webhook/{path_secret}"
        target = "backend"
        start_time = time.perf_counter()
        logger.info(
            "backend_forward_started",
            extra=build_log_extra(
                direction="backend_forward",
                route=route,
                target=target,
                elapsed_ms=0.0,
                status=None,
            ),
        )
        headers = {
            "content-type": "application/json",
            **build_signature_headers(secret=self._shared_secret, body=body),
        }
        request_id = get_request_id()
        if request_id != "-":
            headers[REQUEST_ID_HEADER] = request_id

        try:
            response = await self._http_client.post(
                self._forward_path,
                content=body,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                "backend_forward_completed",
                extra=build_log_extra(
                    direction="backend_forward",
                    route=route,
                    target=target,
                    elapsed_ms=elapsed_ms,
                    status=504,
                    outcome="timeout",
                ),
            )
            return BackendForwardResult(
                ok=False,
                status_code=504,
                error_type="timeout",
                description="backend forward request timed out",
            )
        except httpx.HTTPError as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                "backend_forward_completed",
                extra=build_log_extra(
                    direction="backend_forward",
                    route=route,
                    target=target,
                    elapsed_ms=elapsed_ms,
                    status=502,
                    outcome="network_error",
                ),
            )
            return BackendForwardResult(
                ok=False,
                status_code=502,
                error_type="network_error",
                description="backend forward request failed",
            )

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        if not response.is_success:
            logger.warning(
                "backend_forward_completed",
                extra=build_log_extra(
                    direction="backend_forward",
                    route=route,
                    target=target,
                    elapsed_ms=elapsed_ms,
                    status=response.status_code,
                    outcome="non_2xx",
                ),
            )
            return BackendForwardResult(
                ok=False,
                status_code=response.status_code,
                error_type="non_2xx",
                description="backend forward request failed",
            )

        if response.content:
            try:
                response_payload = response.json()
            except json.JSONDecodeError:
                logger.warning(
                    "backend_forward_completed",
                    extra=build_log_extra(
                        direction="backend_forward",
                        route=route,
                        target=target,
                        elapsed_ms=elapsed_ms,
                        status=502,
                        outcome="invalid_response",
                    ),
                )
                return BackendForwardResult(
                    ok=False,
                    status_code=502,
                    error_type="invalid_response",
                    description="backend forward response was invalid",
                )
            if not isinstance(response_payload, dict):
                logger.warning(
                    "backend_forward_completed",
                    extra=build_log_extra(
                        direction="backend_forward",
                        route=route,
                        target=target,
                        elapsed_ms=elapsed_ms,
                        status=502,
                        outcome="invalid_response",
                    ),
                )
                return BackendForwardResult(
                    ok=False,
                    status_code=502,
                    error_type="invalid_response",
                    description="backend forward response was invalid",
                )

        logger.info(
            "backend_forward_completed",
            extra=build_log_extra(
                direction="backend_forward",
                route=route,
                target=target,
                elapsed_ms=elapsed_ms,
                status=response.status_code,
                outcome="success",
            ),
        )
        return BackendForwardResult(
            ok=True,
            status_code=response.status_code,
            error_type=None,
        )
