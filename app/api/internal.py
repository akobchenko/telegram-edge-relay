from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import get_settings
from app.logging import get_logger
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


@router.post(
    "/sendMessage",
    response_model=TelegramMethodSuccessResponse,
    responses={
        400: {"model": TelegramMethodErrorResponse},
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
        400: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        413: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def send_photo(
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
    chat_id: int | str = Form(...),
    photo: UploadFile = File(...),
    caption: str | None = Form(default=None),
    parse_mode: str | None = Form(default=None),
    disable_notification: bool | None = Form(default=None),
    protect_content: bool | None = Form(default=None),
    message_thread_id: int | None = Form(default=None),
    reply_to_message_id: int | None = Form(default=None),
    allow_sending_without_reply: bool | None = Form(default=None),
) -> TelegramMethodSuccessResponse | JSONResponse:
    try:
        payload = TelegramSendPhotoRequest(
            chat_id=chat_id,
            caption=caption,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            protect_content=protect_content,
            message_thread_id=message_thread_id,
            reply_to_message_id=reply_to_message_id,
            allow_sending_without_reply=allow_sending_without_reply,
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
        400: {"model": TelegramMethodErrorResponse},
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
        400: {"model": TelegramMethodErrorResponse},
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
        400: {"model": TelegramMethodErrorResponse},
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
        400: {"model": TelegramMethodErrorResponse},
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
        400: {"model": TelegramMethodErrorResponse},
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
