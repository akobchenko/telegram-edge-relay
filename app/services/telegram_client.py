from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Literal, cast

import httpx
from fastapi import Request

from app.config import get_settings
from app.logging import build_log_extra, get_logger


@dataclass(frozen=True)
class TelegramApiError(Exception):
    description: str
    error_code: int | None = None
    response_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class TelegramHttpError(Exception):
    description: str
    upstream_status_code: int
    error_code: int | None = None
    response_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class TelegramTransportError(Exception):
    description: str
    error_type: str


TelegramOutboundMode = Literal["typed", "mixed", "proxy"]


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

    async def _post(
        self,
        *,
        method_name: str,
        route: str,
        json_payload: dict[str, Any] | None = None,
        form_payload: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any] | bool:
        bot_token = self._require_bot_token(route=route, operation=method_name)
        target = "telegram"
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
            response = await self._http_client.post(
                f"/bot{bot_token}/{method_name}",
                json=json_payload,
                data=form_payload,
                files=files,
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
        )

    async def call_raw_method(
        self,
        *,
        method_name: str,
        route: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | bool:
        return await self._post(
            method_name=method_name,
            route=route,
            json_payload=payload,
        )

    async def send_message(self, payload: dict[str, Any]) -> dict[str, Any] | bool:
        return await self.call_raw_method(
            method_name="sendMessage",
            route="/internal/telegram/sendMessage",
            payload=payload,
        )

    async def send_photo(
        self,
        *,
        payload: dict[str, Any],
        photo_filename: str,
        photo_content: bytes,
        photo_content_type: str,
    ) -> dict[str, Any] | bool:
        form_payload = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in payload.items()
        }
        return await self._post(
            method_name="sendPhoto",
            route="/internal/telegram/sendPhoto",
            form_payload=form_payload,
            files={
                "photo": (
                    photo_filename,
                    photo_content,
                    photo_content_type,
                )
            },
        )

    async def edit_message_text(self, payload: dict[str, Any]) -> dict[str, Any] | bool:
        return await self.call_raw_method(
            method_name="editMessageText",
            route="/internal/telegram/editMessageText",
            payload=payload,
        )

    async def edit_message_caption(self, payload: dict[str, Any]) -> dict[str, Any] | bool:
        return await self.call_raw_method(
            method_name="editMessageCaption",
            route="/internal/telegram/editMessageCaption",
            payload=payload,
        )

    async def answer_callback_query(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any] | bool:
        return await self.call_raw_method(
            method_name="answerCallbackQuery",
            route="/internal/telegram/answerCallbackQuery",
            payload=payload,
        )

    async def delete_message(self, payload: dict[str, Any]) -> dict[str, Any] | bool:
        return await self.call_raw_method(
            method_name="deleteMessage",
            route="/internal/telegram/deleteMessage",
            payload=payload,
        )

    async def send_chat_action(self, payload: dict[str, Any]) -> dict[str, Any] | bool:
        return await self.call_raw_method(
            method_name="sendChatAction",
            route="/internal/telegram/sendChatAction",
            payload=payload,
        )


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
