from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import IO, Any, Literal, cast

import httpx
from fastapi import Request

from app.config import get_settings
from app.logging import build_log_extra, get_logger


@dataclass(frozen=True)
class TelegramApiError(Exception):
    description: str
    error_code: int | None = None
    response_data: dict[str, Any] | None = None
    response_text: str | None = None


@dataclass(frozen=True)
class TelegramHttpError(Exception):
    description: str
    upstream_status_code: int
    error_code: int | None = None
    response_data: dict[str, Any] | None = None
    response_text: str | None = None


@dataclass(frozen=True)
class TelegramTransportError(Exception):
    description: str
    error_type: str
    response_text: str | None = None


TelegramOutboundMode = Literal["typed", "mixed", "proxy"]
TelegramFormFields = list[tuple[str, str]]
TelegramMultipartFiles = list[tuple[str, tuple[str, IO[bytes] | bytes, str]]]


class TelegramClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        bot_token: str | None,
        outbound_mode: TelegramOutboundMode,
    ) -> None:
        self._http_client = http_client
        self._bot_token = bot_token
        self._outbound_mode = outbound_mode
        self._logger = get_logger("app.telegram.client")

    @property
    def outbound_mode(self) -> TelegramOutboundMode:
        return self._outbound_mode

    def _require_bot_token(self, *, route: str, operation: str) -> str:
        if self._bot_token:
            return self._bot_token
        self._logger.error(
            "telegram_call_completed",
            extra=build_log_extra(
                direction="telegram_outbound",
                route=route,
                target="telegram",
                elapsed_ms=0.0,
                status=503,
                outcome="misconfigured",
                operation=operation,
            ),
        )
        raise TelegramTransportError(
            description="telegram client is not configured",
            error_type="misconfigured",
        )

    def _build_multipart_body(
        self,
        *,
        method_path: str,
        form_fields: TelegramFormFields | None,
        files: TelegramMultipartFiles,
    ) -> tuple[bytes, str]:
        sync_client = httpx.Client(base_url=str(self._http_client.base_url))
        try:
            multipart_request = sync_client.build_request(
                "POST",
                method_path,
                data=form_fields,
                files=files,
            )
            multipart_content_type = multipart_request.headers.get("content-type")
            if multipart_content_type is None:
                raise RuntimeError("failed to build multipart request")
            return multipart_request.read(), multipart_content_type
        finally:
            sync_client.close()

    async def forward_method(
        self,
        *,
        method_name: str,
        route: str,
        json_payload: dict[str, Any] | None = None,
        form_fields: TelegramFormFields | None = None,
        files: TelegramMultipartFiles | None = None,
    ) -> dict[str, Any] | bool:
        bot_token = self._require_bot_token(route=route, operation=method_name)
        target = "telegram"
        method_path = f"/bot{bot_token}/{method_name}"
        start_time = time.perf_counter()
        self._logger.info(
            "telegram_call_started",
            extra=build_log_extra(
                direction="telegram_outbound",
                route=route,
                target=target,
                elapsed_ms=0.0,
                status=None,
                operation=method_name,
                mode=self._outbound_mode,
            ),
        )

        try:
            if files is not None:
                raw_body, multipart_content_type = self._build_multipart_body(
                    method_path=method_path,
                    form_fields=form_fields,
                    files=files,
                )
                response = await self._http_client.post(
                    method_path,
                    content=raw_body,
                    headers={"content-type": multipart_content_type},
                )
            else:
                response = await self._http_client.post(
                    method_path,
                    json=json_payload,
                    data=form_fields,
                )
        except httpx.TimeoutException as exc:
            self._logger.error(
                "telegram_call_completed",
                extra=build_log_extra(
                    direction="telegram_outbound",
                    route=route,
                    target=target,
                    elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    status=504,
                    outcome="timeout",
                    operation=method_name,
                    mode=self._outbound_mode,
                ),
            )
            raise TelegramTransportError(
                description="telegram request timed out",
                error_type="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            self._logger.error(
                "telegram_call_completed",
                extra=build_log_extra(
                    direction="telegram_outbound",
                    route=route,
                    target=target,
                    elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    status=502,
                    outcome="network_error",
                    operation=method_name,
                    mode=self._outbound_mode,
                ),
            )
            raise TelegramTransportError(
                description="telegram transport error",
                error_type="network_error",
            ) from exc
        except RuntimeError as exc:
            self._logger.error(
                "telegram_call_completed",
                extra=build_log_extra(
                    direction="telegram_outbound",
                    route=route,
                    target=target,
                    elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    status=502,
                    outcome="network_error",
                    operation=method_name,
                    mode=self._outbound_mode,
                ),
            )
            raise TelegramTransportError(
                description="telegram transport error",
                error_type="network_error",
                response_text=str(exc),
            ) from exc

        response_text = response.text
        try:
            data = response.json()
        except ValueError as exc:
            self._logger.error(
                "telegram_call_completed",
                extra=build_log_extra(
                    direction="telegram_outbound",
                    route=route,
                    target=target,
                    elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    status=502,
                    outcome="invalid_response",
                    operation=method_name,
                    mode=self._outbound_mode,
                ),
            )
            raise TelegramTransportError(
                description="telegram returned a malformed response",
                error_type="invalid_response",
                response_text=response_text,
            ) from exc

        if response.is_success and isinstance(data, dict) and data.get("ok") is True:
            result = data.get("result")
            if isinstance(result, (dict, bool)):
                self._logger.info(
                    "telegram_call_completed",
                    extra=build_log_extra(
                        direction="telegram_outbound",
                        route=route,
                        target=target,
                        elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                        status=response.status_code,
                        outcome="success",
                        operation=method_name,
                        mode=self._outbound_mode,
                    ),
                )
                return result
            self._logger.error(
                "telegram_call_completed",
                extra=build_log_extra(
                    direction="telegram_outbound",
                    route=route,
                    target=target,
                    elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    status=502,
                    outcome="invalid_response",
                    operation=method_name,
                    mode=self._outbound_mode,
                ),
            )
            raise TelegramTransportError(
                description="telegram success response is malformed",
                error_type="invalid_response",
                response_text=response_text,
            )

        if not response.is_success:
            if isinstance(data, dict):
                description = str(data.get("description", "telegram http error"))
                error_code = data.get("error_code")
                response_data = data
            else:
                description = "telegram http error"
                error_code = None
                response_data = None
            self._logger.warning(
                "telegram_call_completed",
                extra=build_log_extra(
                    direction="telegram_outbound",
                    route=route,
                    target=target,
                    elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    status=response.status_code,
                    outcome="telegram_http_error",
                    operation=method_name,
                    mode=self._outbound_mode,
                    upstream_status_code=response.status_code,
                    error_code=error_code if isinstance(error_code, int) else None,
                ),
            )
            raise TelegramHttpError(
                description=description,
                upstream_status_code=response.status_code,
                error_code=error_code if isinstance(error_code, int) else None,
                response_data=response_data,
                response_text=None if isinstance(data, dict) else response_text,
            )

        if isinstance(data, dict):
            description = str(data.get("description", "telegram api error"))
            error_code = data.get("error_code")
            response_data = data
        else:
            description = "telegram api error"
            error_code = None
            response_data = None

        self._logger.warning(
            "telegram_call_completed",
            extra=build_log_extra(
                direction="telegram_outbound",
                route=route,
                target=target,
                elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
                status=502,
                outcome="telegram_api_error",
                operation=method_name,
                mode=self._outbound_mode,
                error_code=error_code if isinstance(error_code, int) else None,
            ),
        )
        raise TelegramApiError(
            description=description,
            error_code=error_code if isinstance(error_code, int) else None,
            response_data=response_data,
            response_text=response_text,
        )

    @staticmethod
    def serialize_form_fields(payload: dict[str, Any]) -> TelegramFormFields:
        fields: TelegramFormFields = []
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                normalized = json.dumps(value, separators=(",", ":"))
            elif isinstance(value, bool):
                normalized = str(value).lower()
            else:
                normalized = str(value)
            fields.append((key, normalized))
        return fields

def get_telegram_client(request: Request) -> TelegramClient:
    return cast(TelegramClient, request.app.state.telegram_client)


def build_telegram_http_client() -> httpx.AsyncClient:
    settings = get_settings()
    timeout = httpx.Timeout(
        settings.telegram_timeout_seconds,
        connect=min(settings.telegram_timeout_seconds, 5.0),
    )
    return httpx.AsyncClient(
        base_url="https://api.telegram.org",
        timeout=timeout,
    )
