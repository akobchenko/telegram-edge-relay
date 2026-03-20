from __future__ import annotations

from collections.abc import Awaitable
import json
import re
from typing import Any

from fastapi import Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import get_settings
from app.logging import get_logger
from app.models.internal import (
    TelegramMethodErrorResponse,
    TelegramMethodSuccessResponse,
    TelegramSendPhotoRequest,
)
from app.services.telegram_client import (
    TelegramApiError,
    TelegramClient,
    TelegramFormFields,
    TelegramHttpError,
    TelegramMultipartFiles,
    TelegramTransportError,
)

logger = get_logger("app.internal.telegram")
_RAW_METHOD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,63}$")


class MultipartForwardError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _first_form_field_value(form_fields: TelegramFormFields, key: str) -> str | None:
    for field_name, field_value in form_fields:
        if field_name == key:
            return field_value
    return None


async def _forward_canonical_request(
    *,
    method_name: str,
    route: str,
    telegram_client: TelegramClient,
    json_payload: dict[str, Any] | None = None,
    form_fields: TelegramFormFields | None = None,
    files: TelegramMultipartFiles | None = None,
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await run_telegram_call(
        telegram_client.forward_method(
            method_name=method_name,
            route=route,
            json_payload=json_payload,
            form_fields=form_fields,
            files=files,
        )
    )


def build_internal_error_response(
    *,
    error_type: str,
    message: str,
    status_code: int,
    telegram_status_code: int | None = None,
    telegram_error_code: int | None = None,
    telegram_description: str | None = None,
    telegram_response: dict[str, Any] | None = None,
    telegram_response_text: str | None = None,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    error_response = TelegramMethodErrorResponse(
        ok=False,
        error_type=error_type,
        message=message,
        status_code=status_code,
        telegram_status_code=telegram_status_code,
        telegram_error_code=telegram_error_code,
        telegram_description=telegram_description,
        telegram_response=telegram_response,
        telegram_response_text=telegram_response_text,
        details=details,
    )
    return JSONResponse(status_code=status_code, content=error_response.model_dump())


def _response_mode() -> str:
    return get_settings().telegram_response_mode


def _build_transparent_error_response(
    *,
    status_code: int,
    description: str,
    error_code: int | None = None,
    response_data: dict[str, Any] | None = None,
    response_text: str | None = None,
    error_type: str,
) -> JSONResponse:
    if response_data is not None:
        return JSONResponse(status_code=status_code, content=response_data)

    payload: dict[str, Any] = {
        "ok": False,
        "description": description,
        "error_type": error_type,
    }
    if error_code is not None:
        payload["error_code"] = error_code
    if response_text is not None:
        payload["raw_response_text"] = response_text
    return JSONResponse(status_code=status_code, content=payload)


def _build_transport_error_response(
    *,
    error_type: str,
    message: str,
    status_code: int,
    response_text: str | None = None,
) -> JSONResponse:
    if _response_mode() == "transparent":
        return _build_transparent_error_response(
            status_code=status_code,
            description=message,
            error_code=status_code,
            response_text=response_text,
            error_type=error_type,
        )
    return build_internal_error_response(
        error_type=error_type,
        message=message,
        status_code=status_code,
        telegram_response_text=response_text,
    )


def _build_upstream_error_response(
    *,
    error_type: str,
    message: str,
    status_code: int,
    telegram_status_code: int | None = None,
    telegram_error_code: int | None = None,
    telegram_description: str | None = None,
    telegram_response: dict[str, Any] | None = None,
    telegram_response_text: str | None = None,
) -> JSONResponse:
    if _response_mode() == "transparent":
        return _build_transparent_error_response(
            status_code=status_code,
            description=telegram_description or message,
            error_code=telegram_error_code,
            response_data=telegram_response,
            response_text=telegram_response_text,
            error_type=error_type,
        )
    return build_internal_error_response(
        error_type=error_type,
        message=message,
        status_code=status_code,
        telegram_status_code=telegram_status_code,
        telegram_error_code=telegram_error_code,
        telegram_description=telegram_description,
        telegram_response=telegram_response,
        telegram_response_text=telegram_response_text,
    )


def _telegram_error_response(
    exc: TelegramApiError | TelegramHttpError | TelegramTransportError,
) -> JSONResponse:
    if isinstance(exc, TelegramHttpError):
        logger.warning(
            "telegram_method_failed",
            extra={
                "direction": "telegram_outbound",
                "target_system": "telegram",
                "outcome": "telegram_http_error",
                "upstream_status_code": exc.upstream_status_code,
                "error_code": exc.error_code,
            },
        )
        return _build_upstream_error_response(
            error_type="telegram_http_error",
            message="telegram returned a non-2xx response",
            status_code=exc.upstream_status_code,
            telegram_status_code=exc.upstream_status_code,
            telegram_error_code=exc.error_code,
            telegram_description=exc.description,
            telegram_response=exc.response_data,
            telegram_response_text=exc.response_text,
        )

    if isinstance(exc, TelegramApiError):
        logger.warning(
            "telegram_method_failed",
            extra={
                "direction": "telegram_outbound",
                "target_system": "telegram",
                "outcome": "telegram_api_error",
                "error_code": exc.error_code,
            },
        )
        return _build_upstream_error_response(
            error_type="telegram_api_error",
            message="telegram returned an application error",
            status_code=200,
            telegram_error_code=exc.error_code,
            telegram_description=exc.description,
            telegram_response=exc.response_data,
            telegram_response_text=exc.response_text,
        )

    logger.error(
        "telegram_method_failed",
        extra={
            "direction": "telegram_outbound",
            "target_system": "telegram",
            "outcome": exc.error_type,
        },
    )
    if exc.error_type == "timeout":
        return _build_transport_error_response(
            error_type="relay_timeout",
            message=exc.description,
            status_code=504,
            response_text=exc.response_text,
        )
    return _build_transport_error_response(
        error_type="relay_network_error",
        message=exc.description,
        status_code=503 if exc.error_type == "misconfigured" else 502,
        response_text=exc.response_text,
    )


async def run_telegram_call(
    call: Awaitable[dict[str, Any] | bool],
) -> TelegramMethodSuccessResponse | JSONResponse:
    try:
        result = await call
    except (TelegramApiError, TelegramHttpError, TelegramTransportError) as exc:
        return _telegram_error_response(exc)

    logger.info(
        "telegram_method_succeeded",
        extra={
            "direction": "telegram_outbound",
            "target_system": "telegram",
            "outcome": "success",
        },
    )
    return TelegramMethodSuccessResponse(ok=True, result=result)


def raw_method_route(method: str) -> str:
    return f"/internal/telegram/raw/{method}"


def validate_raw_method_name(method: str) -> str | None:
    if _RAW_METHOD_PATTERN.fullmatch(method):
        return None
    return "telegram method name is invalid"


def require_raw_mode_allowed(mode: str) -> JSONResponse | None:
    if mode != "typed":
        return None
    return build_internal_error_response(
        error_type="operation_not_allowed",
        message="raw telegram fallback is disabled in typed mode",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def build_multipart_forward_payload(
    form: Any,
) -> tuple[TelegramFormFields, TelegramMultipartFiles]:
    fields: TelegramFormFields = []
    files: TelegramMultipartFiles = []
    for key, value in form.multi_items():
        if isinstance(value, UploadFile) or (
            hasattr(value, "file")
            and hasattr(value, "filename")
            and hasattr(value, "content_type")
        ):
            files.append(
                (
                    key,
                    (
                        value.filename or key,
                        _read_upload_file_bytes(value),
                        value.content_type or "application/octet-stream",
                    ),
                )
            )
        else:
            fields.append((key, value if isinstance(value, str) else str(value)))
    return fields, files


def _read_upload_file_bytes(upload: Any) -> bytes:
    file_object = getattr(upload, "file", None)
    if file_object is None or not hasattr(file_object, "read"):
        raise MultipartForwardError("uploaded file is invalid")
    try:
        current_position = file_object.tell()
        file_object.seek(0)
        file_bytes = file_object.read()
        file_object.seek(current_position)
    except Exception as exc:
        raise MultipartForwardError("uploaded file could not be read") from exc
    if not isinstance(file_bytes, bytes):
        raise MultipartForwardError("uploaded file could not be read")
    return file_bytes


async def parse_form_data(request: Request) -> Any:
    body = await request.body()
    receive_complete = False

    async def receive() -> dict[str, Any]:
        nonlocal receive_complete
        if receive_complete:
            return {"type": "http.request", "body": b"", "more_body": False}
        receive_complete = True
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        replay_request = Request(request.scope, receive)
        return await replay_request.form()
    except Exception as exc:
        raise MultipartForwardError("failed to parse form data") from exc


async def forward_typed_json_method(
    *,
    method_name: str,
    payload: dict[str, Any],
    telegram_client: TelegramClient,
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await _forward_canonical_request(
        method_name=method_name,
        route=f"/internal/telegram/{method_name}",
        telegram_client=telegram_client,
        json_payload=payload,
    )


def request_content_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";", 1)[0].strip().lower()


async def forward_raw_request(
    *,
    request: Request,
    method_name: str,
    route: str,
    telegram_client: TelegramClient,
    json_object_required: bool,
) -> TelegramMethodSuccessResponse | JSONResponse:
    content_type = request_content_type(request)

    if content_type == "application/json":
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return build_internal_error_response(
                error_type="validation_error",
                message="request body must be a JSON object",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if json_object_required and not isinstance(payload, dict):
            return build_internal_error_response(
                error_type="validation_error",
                message="request body must be a JSON object",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if not isinstance(payload, dict):
            return build_internal_error_response(
                error_type="validation_error",
                message="request body must be a JSON object",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return await _forward_canonical_request(
            method_name=method_name,
            route=route,
            telegram_client=telegram_client,
            json_payload=payload,
        )

    if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        try:
            form = await parse_form_data(request)
            form_fields, files = build_multipart_forward_payload(form)
        except MultipartForwardError as exc:
            logger.warning(
                "telegram_raw_method_failed",
                extra={
                    "direction": "telegram_outbound",
                    "target_system": "relay",
                    "outcome": "validation_error",
                    "operation": method_name,
                },
            )
            return build_internal_error_response(
                error_type="validation_error",
                message=exc.detail,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return await _forward_canonical_request(
            method_name=method_name,
            route=route,
            telegram_client=telegram_client,
            form_fields=form_fields,
            files=files or None,
        )

    return build_internal_error_response(
        error_type="validation_error",
        message="unsupported content type",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


async def forward_send_photo(
    *,
    request: Request,
    telegram_client: TelegramClient,
) -> TelegramMethodSuccessResponse | JSONResponse:
    try:
        form = await parse_form_data(request)
        form_fields, files = build_multipart_forward_payload(form)
    except MultipartForwardError as exc:
        return build_internal_error_response(
            error_type="validation_error",
            message=exc.detail,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    photo_files = [file_payload for field_name, file_payload in files if field_name == "photo"]
    if not photo_files:
        raise RequestValidationError(
            [
                {
                    "type": "missing",
                    "loc": ("body", "photo"),
                    "msg": "Field required",
                    "input": None,
                }
            ]
        )
    _, photo_bytes, _ = photo_files[0]

    reply_markup_raw = _first_form_field_value(form_fields, "reply_markup")
    reply_markup = None
    if reply_markup_raw not in (None, ""):
        try:
            reply_markup = json.loads(reply_markup_raw)
        except json.JSONDecodeError:
            return build_internal_error_response(
                error_type="validation_error",
                message="reply_markup must be valid JSON",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if not isinstance(reply_markup, dict):
            return build_internal_error_response(
                error_type="validation_error",
                message="reply_markup must be a JSON object",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

    try:
        TelegramSendPhotoRequest(
            chat_id=_first_form_field_value(form_fields, "chat_id"),
            caption=_first_form_field_value(form_fields, "caption"),
            parse_mode=_first_form_field_value(form_fields, "parse_mode"),
            disable_notification=_first_form_field_value(
                form_fields, "disable_notification"
            ),
            protect_content=_first_form_field_value(form_fields, "protect_content"),
            message_thread_id=_first_form_field_value(
                form_fields, "message_thread_id"
            ),
            reply_to_message_id=_first_form_field_value(
                form_fields, "reply_to_message_id"
            ),
            allow_sending_without_reply=_first_form_field_value(
                form_fields, "allow_sending_without_reply"
            ),
            reply_markup=reply_markup,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    photo_size = len(photo_bytes)
    if photo_size <= 0:
        return build_internal_error_response(
            error_type="validation_error",
            message="photo file must not be empty",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    settings = get_settings()
    if (
        settings.telegram_photo_max_bytes is not None
        and photo_size > settings.telegram_photo_max_bytes
    ):
        return build_internal_error_response(
            error_type="validation_error",
            message="photo file exceeds configured size limit",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    return await _forward_canonical_request(
        method_name="sendPhoto",
        route="/internal/telegram/sendPhoto",
        telegram_client=telegram_client,
        form_fields=form_fields,
        files=files,
    )


__all__ = [
    "build_internal_error_response",
    "build_multipart_forward_payload",
    "forward_raw_request",
    "forward_send_photo",
    "forward_typed_json_method",
    "logger",
    "parse_form_data",
    "raw_method_route",
    "require_raw_mode_allowed",
    "run_telegram_call",
    "validate_raw_method_name",
]
