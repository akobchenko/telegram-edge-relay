from __future__ import annotations

import io
import logging

from app.logging import PlainTextFormatter, configure_logging


def test_plain_text_formatter_falls_back_to_context_request_id() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        PlainTextFormatter(
            "%(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
        )
    )

    logger = logging.getLogger("tests.logging")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("hello")

    output = stream.getvalue()
    assert "hello" in output
    assert "request_id=-" in output


def test_configure_logging_suppresses_httpx_info_logs() -> None:
    configure_logging(level="INFO", json_logs=True)
    assert logging.getLogger("httpx").level == logging.WARNING
