from __future__ import annotations

from contextvars import ContextVar
import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("app.request")


def get_request_id() -> str:
    return _request_id_context.get()


def normalize_request_id(value: str | None) -> str:
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


def request_direction(path: str) -> str:
    if path.startswith("/internal/telegram"):
        return "telegram_outbound"
    if path.startswith("/telegram"):
        return "telegram_inbound"
    return "system"


def build_request_log_extra(
    *,
    direction: str,
    route: str,
    method: str,
    elapsed_ms: float,
    status: int | None,
) -> dict[str, object]:
    return {
        "direction": direction,
        "route": route,
        "target": "relay",
        "elapsed_ms": elapsed_ms,
        "status": status,
        "method": method,
    }


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header_map = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        request_id = normalize_request_id(header_map.get(REQUEST_ID_HEADER.lower()))
        route = scope.get("path", "")
        method = scope.get("method", "UNKNOWN")
        direction = request_direction(route)
        start_time = time.perf_counter()
        status_code = 500
        token = _request_id_context.set(request_id)
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        logger.info(
            "request_started",
            extra=build_request_log_extra(
                direction=direction,
                route=route,
                elapsed_ms=0.0,
                status=None,
                method=method,
            ),
        )

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(
                "request_completed",
                extra=build_request_log_extra(
                    direction=direction,
                    route=route,
                    elapsed_ms=elapsed_ms,
                    status=status_code,
                    method=method,
                ),
            )
            _request_id_context.reset(token)
