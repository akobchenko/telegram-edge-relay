from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import get_settings
from app.models.public import TelegramWebhookAcceptedResponse
from app.services.backend_forwarder import BackendForwarder

router = APIRouter(tags=["public"])


def get_backend_forwarder(request: Request) -> BackendForwarder:
    return request.app.state.backend_forwarder  # type: ignore[no-any-return]


@router.post(
    "/telegram/webhook/{path_secret}",
    response_model=TelegramWebhookAcceptedResponse,
    responses={
        403: {"description": "invalid webhook secret"},
        422: {"description": "invalid update payload"},
        502: {"description": "backend forward failed"},
        504: {"description": "backend forward timed out"},
    },
)
async def telegram_webhook(
    path_secret: str,
    request: Request,
    backend_forwarder: BackendForwarder = Depends(get_backend_forwarder),
) -> TelegramWebhookAcceptedResponse:
    settings = get_settings()
    if not hmac.compare_digest(
        path_secret,
        settings.telegram_webhook_path_secret.get_secret_value(),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid webhook secret",
        )

    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request body must be a JSON object",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request body must be a JSON object",
        )

    result = await backend_forwarder.forward_telegram_update(body)
    if not result.ok:
        status_code = 504 if result.error_type == "timeout" else 502
        raise HTTPException(
            status_code=status_code,
            detail=result.description or "backend forward request failed",
        )

    return TelegramWebhookAcceptedResponse(ok=True)
