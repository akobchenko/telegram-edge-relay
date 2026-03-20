from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.config import HealthConfigSummary


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    config: HealthConfigSummary


class VersionResponse(BaseModel):
    app_name: str
    version: str
