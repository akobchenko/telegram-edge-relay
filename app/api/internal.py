from __future__ import annotations

from collections.abc import Awaitable
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import get_settings
from app.logging import build_log_extra, get_logger
from app.models.internal import (
    TelegramAnswerCallbackQueryRequest,
    TelegramDeleteMessageRequest,
    TelegramEditMessageCaptionRequest,
    TelegramEditMessageTextRequest,
    TelegramMethodErrorResponse,
    TelegramMethodSuccessResponse,
    TelegramSendChatActionRequest,
    TelegramSendMessageRequest,
    TelegramSendPhotoRequest,
)
from app.security.signing import require_internal_signature
from app.services.telegram_client import (
    TelegramApiError,
    TelegramClient,
    TelegramHttpError,
    TelegramTransportError,
    get_telegram_client,
)

router = APIRouter(prefix="/internal/telegram", tags=["internal"])
logger = get_logger("app.internal.telegram")
_RAW_METHOD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,63}$")


def build_internal_error_response(
    *,
    error_type: str,
    message: str,
    status_code: int,
    telegram_status_code: int | None = None,
    telegram_error_code: int | None = None,
    telegram_description: str | None = None,
    telegram_response: dict[str, Any] | None = None,
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
        details=details,
    )
    return JSONResponse(status_code=status_code, content=error_response.model_dump())


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
        return build_internal_error_response(
            error_type="telegram_http_error",
            message="telegram returned a non-2xx response",
            status_code=exc.upstream_status_code,
            telegram_status_code=exc.upstream_status_code,
            telegram_error_code=exc.error_code,
            telegram_description=exc.description,
            telegram_response=exc.response_data,
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
        return build_internal_error_response(
            error_type="telegram_api_error",
            message="telegram returned an application error",
            status_code=502,
            telegram_error_code=exc.error_code,
            telegram_description=exc.description,
            telegram_response=exc.response_data,
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
        return build_internal_error_response(
            error_type="relay_timeout",
            message=exc.description,
            status_code=504,
        )
    return build_internal_error_response(
        error_type="relay_network_error",
        message=exc.description,
        status_code=503 if exc.error_type == "misconfigured" else 502,
    )


async def _run_telegram_call(
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


def _raw_method_route(method: str) -> str:
    return f"/internal/telegram/raw/{method}"


def _validate_raw_method_name(method: str) -> str | None:
    if _RAW_METHOD_PATTERN.fullmatch(method):
        return None
    return "telegram method name is invalid"


def _require_raw_mode_allowed(mode: str) -> JSONResponse | None:
    if mode != "typed":
        return None
    return build_internal_error_response(
        error_type="operation_not_allowed",
        message="raw telegram fallback is disabled in typed mode",
        status_code=status.HTTP_403_FORBIDDEN,
    )


@router.post(
    "/sendMessage",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def send_message(
    payload: TelegramSendMessageRequest,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await _run_telegram_call(
        telegram_client.send_message(payload.model_dump(exclude_none=True))
    )


@router.post(
    "/sendPhoto",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        413: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def send_photo(
    request: Request,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    form = await request.form()
    photo = form.get("photo")
    if not isinstance(photo, UploadFile) and not (
        hasattr(photo, "read") and hasattr(photo, "filename")
    ):
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

    reply_markup_raw = form.get("reply_markup")
    reply_markup = None
    if reply_markup_raw not in (None, ""):
        if not isinstance(reply_markup_raw, str):
            return build_internal_error_response(
                error_type="validation_error",
                message="reply_markup must be valid JSON",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
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
        payload = TelegramSendPhotoRequest(
            chat_id=form.get("chat_id"),
            caption=form.get("caption"),
            parse_mode=form.get("parse_mode"),
            disable_notification=form.get("disable_notification"),
            protect_content=form.get("protect_content"),
            message_thread_id=form.get("message_thread_id"),
            reply_to_message_id=form.get("reply_to_message_id"),
            allow_sending_without_reply=form.get("allow_sending_without_reply"),
            reply_markup=reply_markup,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    photo_content = await photo.read()
    if not photo_content:
        return build_internal_error_response(
            error_type="validation_error",
            message="photo file must not be empty",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    settings = get_settings()
    if (
        settings.telegram_photo_max_bytes is not None
        and len(photo_content) > settings.telegram_photo_max_bytes
    ):
        return build_internal_error_response(
            error_type="validation_error",
            message="photo file exceeds configured size limit",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    content_type = photo.content_type or "application/octet-stream"
    filename = photo.filename or "photo"
    return await _run_telegram_call(
        telegram_client.send_photo(
            payload=payload.model_dump(exclude_none=True),
            photo_filename=filename,
            photo_content=photo_content,
            photo_content_type=content_type,
        )
    )


@router.post(
    "/editMessageText",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def edit_message_text(
    payload: TelegramEditMessageTextRequest,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await _run_telegram_call(
        telegram_client.edit_message_text(payload.model_dump(exclude_none=True))
    )


@router.post(
    "/editMessageCaption",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def edit_message_caption(
    payload: TelegramEditMessageCaptionRequest,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await _run_telegram_call(
        telegram_client.edit_message_caption(payload.model_dump(exclude_none=True))
    )


@router.post(
    "/answerCallbackQuery",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def answer_callback_query(
    payload: TelegramAnswerCallbackQueryRequest,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await _run_telegram_call(
        telegram_client.answer_callback_query(payload.model_dump(exclude_none=True))
    )


@router.post(
    "/deleteMessage",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def delete_message(
    payload: TelegramDeleteMessageRequest,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await _run_telegram_call(
        telegram_client.delete_message(payload.model_dump(exclude_none=True))
    )


@router.post(
    "/sendChatAction",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def send_chat_action(
    payload: TelegramSendChatActionRequest,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await _run_telegram_call(
        telegram_client.send_chat_action(payload.model_dump(exclude_none=True))
    )


@router.post(
    "/raw/{method}",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        403: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def call_raw_method(
    method: str,
    payload: dict[str, Any],
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    route = _raw_method_route(method)

    invalid_method_message = _validate_raw_method_name(method)
    if invalid_method_message is not None:
        return build_internal_error_response(
            error_type="validation_error",
            message=invalid_method_message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    mode_rejection = _require_raw_mode_allowed(telegram_client.outbound_mode)
    if mode_rejection is not None:
        logger.warning(
            "telegram_raw_method_rejected",
            extra=build_log_extra(
                direction="telegram_outbound",
                route=route,
                target="telegram",
                elapsed_ms=0.0,
                status=403,
                outcome="operation_not_allowed",
                operation=method,
                mode=telegram_client.outbound_mode,
            ),
        )
        return mode_rejection

    logger.info(
        "telegram_raw_method_requested",
        extra=build_log_extra(
            direction="telegram_outbound",
            route=route,
            target="telegram",
            elapsed_ms=0.0,
            status=None,
            operation=method,
            mode=telegram_client.outbound_mode,
        ),
    )
    return await _run_telegram_call(
        telegram_client.call_raw_method(
            method_name=method,
            route=route,
            payload=payload,
        )
    )
