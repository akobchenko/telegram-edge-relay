from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.internal import router as internal_router
from app.api.internal import build_internal_error_response
from app.api.public import router as public_router
from app.api.system import router as system_router
from app.config import get_settings
from app.core.request_id import RequestIdMiddleware
from app.logging import configure_logging
from app.services.backend_forwarder import BackendForwarder
from app.services.telegram_client import TelegramClient, build_telegram_http_client

import httpx


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=not settings.debug)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        backend_timeout = httpx.Timeout(
            settings.backend_timeout_seconds,
            connect=min(settings.backend_timeout_seconds, 5.0),
        )
        async with build_telegram_http_client() as telegram_http_client, httpx.AsyncClient(
            base_url=str(settings.backend_base_url).rstrip("/"),
            timeout=backend_timeout,
        ) as backend_http_client:
            app.state.settings = settings
            app.state.telegram_client = TelegramClient(
                http_client=telegram_http_client,
                bot_token=settings.telegram_bot_token.get_secret_value(),
            )
            app.state.backend_forwarder = BackendForwarder(
                http_client=backend_http_client,
                shared_secret=settings.internal_shared_secret.get_secret_value(),
                forward_path=settings.backend_forward_path,
            )
            yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(internal_router)
    app.include_router(public_router)
    app.include_router(system_router)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(request, exc: RequestValidationError):
        if request.url.path.startswith("/internal/telegram"):
            return build_internal_error_response(
                error_type="validation_error",
                message="request validation failed",
                status_code=422,
                details=exc.errors(),
            )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request, exc: HTTPException):
        if request.url.path.startswith("/internal/telegram") and exc.status_code == 401:
            return build_internal_error_response(
                error_type="auth_error",
                message=str(exc.detail),
                status_code=401,
            )
        return await http_exception_handler(request, exc)

    return app


app = create_app()
