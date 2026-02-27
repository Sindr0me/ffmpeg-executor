from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, HttpUrl, field_validator


# ── Request ──────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    preset: str
    preset_options: dict[str, Any] = {}
    input_url: str
    output_filename: str
    webhook_url: Optional[str] = None
    metadata: dict[str, Any] = {}

    @field_validator("input_url")
    @classmethod
    def must_be_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("input_url must start with https://")
        return v

    @field_validator("output_filename")
    @classmethod
    def safe_filename(cls, v: str) -> str:
        import re
        if not re.match(r'^[\w\-. ]+$', v):
            raise ValueError("output_filename contains invalid characters")
        return v


# ── Response ─────────────────────────────────────────────────────────────────

class JobCreated(BaseModel):
    job_id: uuid.UUID


class JobResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    stage: Optional[str] = None
    preset: str
    output_url: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: str
    api: bool
    redis: bool
    postgres: bool
