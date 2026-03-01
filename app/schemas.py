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
    output_presigned_url: Optional[str] = None  # if set, upload result here instead of R2

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
        if not re.match(r"^[\w\-. ]+$", v):
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

# ── Command (Rendi-style) ─────────────────────────────────────────────────────

class CommandCreate(BaseModel):
    ffmpeg_command: str
    input_files: dict[str, str] = {}    # {"in_video": "https://..."}
    output_files: dict[str, str] = {}   # {"out_result": "output.mp4"}
    webhook_url: Optional[str] = None
    output_presigned_urls: Optional[dict[str, str]] = None  # {alias: presigned_url}

    @field_validator("input_files")
    @classmethod
    def validate_input_aliases(cls, v: dict) -> dict:
        for key in v:
            if not key.startswith("in_"):
                raise ValueError(f"input_files keys must start with in_, got: {key}")
        return v

    @field_validator("output_files")
    @classmethod
    def validate_output_aliases(cls, v: dict) -> dict:
        import re
        for key, filename in v.items():
            if not key.startswith("out_"):
                raise ValueError(f"output_files keys must start with out_, got: {key}")
            if not re.match(r"^[\w\-. ]+$", filename):
                raise ValueError(f"output filename {filename} contains invalid characters")
        return v


class CommandCreated(BaseModel):
    command_id: uuid.UUID


class OutputFileResult(BaseModel):
    url: str
    size_bytes: Optional[int] = None


class CommandResponse(BaseModel):
    command_id: uuid.UUID
    status: str
    stage: Optional[str] = None
    ffmpeg_command: str
    input_files: dict[str, str] = {}
    output_files: dict[str, OutputFileResult] = {}
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None

    model_config = {"from_attributes": True}
