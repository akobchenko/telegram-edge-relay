from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TelegramWebhookAcceptedResponse(BaseModel):
    ok: Literal[True]
