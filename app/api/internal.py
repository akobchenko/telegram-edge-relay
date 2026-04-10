from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi import status
from fastapi.responses import JSONResponse, Response

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
)
from app.security.signing import require_internal_signature
from app.services.telegram_client import (
    TelegramClient,
    get_telegram_client,
)
from app.services.internal_telegram import (
    build_internal_error_response,
    forward_file_download,
    forward_raw_request,
    forward_send_photo,
    forward_typed_json_method,
    raw_method_route,
    require_raw_mode_allowed,
    validate_raw_method_name,
)

router = APIRouter(prefix="/internal/telegram", tags=["internal"])
logger = get_logger("app.internal.telegram")


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
    return await forward_typed_json_method(
        method_name="sendMessage",
        payload=payload.model_dump(exclude_none=True),
        telegram_client=telegram_client,
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
    return await forward_send_photo(request=request, telegram_client=telegram_client)


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
    return await forward_typed_json_method(
        method_name="editMessageText",
        payload=payload.model_dump(exclude_none=True),
        telegram_client=telegram_client,
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
    return await forward_typed_json_method(
        method_name="editMessageCaption",
        payload=payload.model_dump(exclude_none=True),
        telegram_client=telegram_client,
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
    return await forward_typed_json_method(
        method_name="answerCallbackQuery",
        payload=payload.model_dump(exclude_none=True),
        telegram_client=telegram_client,
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
    return await forward_typed_json_method(
        method_name="deleteMessage",
        payload=payload.model_dump(exclude_none=True),
        telegram_client=telegram_client,
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
    return await forward_typed_json_method(
        method_name="sendChatAction",
        payload=payload.model_dump(exclude_none=True),
        telegram_client=telegram_client,
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
    request: Request,
    method: str,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    route = raw_method_route(method)

    invalid_method_message = validate_raw_method_name(method)
    if invalid_method_message is not None:
        return build_internal_error_response(
            error_type="validation_error",
            message=invalid_method_message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    mode_rejection = require_raw_mode_allowed(telegram_client.outbound_mode)
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
    return await forward_raw_request(
        request=request,
        method_name=method,
        route=route,
        telegram_client=telegram_client,
        json_object_required=True,
    )


@router.post(
    "/editMessageMedia",
    response_model=TelegramMethodSuccessResponse,
    responses={
        422: {"model": TelegramMethodErrorResponse},
        401: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def edit_message_media(
    request: Request,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    return await forward_raw_request(
        request=request,
        method_name="editMessageMedia",
        route="/internal/telegram/editMessageMedia",
        telegram_client=telegram_client,
        json_object_required=True,
    )


@router.get(
    "/file/{file_path:path}",
    responses={
        401: {"model": TelegramMethodErrorResponse},
        422: {"model": TelegramMethodErrorResponse},
        500: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def download_file(
    file_path: str,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> Response | JSONResponse:
    return await forward_file_download(
        file_path=file_path,
        telegram_client=telegram_client,
    )


@router.post(
    "/{method}",
    response_model=TelegramMethodSuccessResponse,
    responses={
        401: {"model": TelegramMethodErrorResponse},
        403: {"model": TelegramMethodErrorResponse},
        422: {"model": TelegramMethodErrorResponse},
        500: {"model": TelegramMethodErrorResponse},
        502: {"model": TelegramMethodErrorResponse},
        503: {"model": TelegramMethodErrorResponse},
        504: {"model": TelegramMethodErrorResponse},
    },
)
async def call_canonical_method(
    request: Request,
    method: str,
    _: None = Depends(require_internal_signature),
    telegram_client: TelegramClient = Depends(get_telegram_client),
) -> TelegramMethodSuccessResponse | JSONResponse:
    route = f"/internal/telegram/{method}"

    invalid_method_message = validate_raw_method_name(method)
    if invalid_method_message is not None:
        return build_internal_error_response(
            error_type="validation_error",
            message=invalid_method_message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    mode_rejection = require_raw_mode_allowed(telegram_client.outbound_mode)
    if mode_rejection is not None:
        logger.warning(
            "telegram_method_rejected",
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
        "telegram_method_requested",
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
    return await forward_raw_request(
        request=request,
        method_name=method,
        route=route,
        telegram_client=telegram_client,
        json_object_required=True,
    )
