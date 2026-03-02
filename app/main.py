"""FastAPI application: routes and startup."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import Base, Job, JobStatus, Command
from app.schemas import (
    HealthResponse, JobCreate, JobCreated, JobResponse,
    CommandCreate, CommandCreated, CommandResponse, OutputFileResult
)
from app.security import URLSecurityError, validate_input_url, validate_ffmpeg_command, CommandSecurityError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Async DB setup ────────────────────────────────────────────────────────────

async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="FFmpeg Executor API",
    description="Internal video processing service for n8n",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/jobs", response_model=JobCreated, status_code=202)
@limiter.limit("20/minute")
async def create_job(request: Request, payload: JobCreate):
    """Submit a new video processing job."""
    # Validate input URL (SSRF protection)
    try:
        validate_input_url(payload.input_url)
    except URLSecurityError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Validate preset exists
    from app.presets import get_preset, PRESETS
    try:
        get_preset(payload.preset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async with AsyncSessionLocal() as db:
        job = Job(
            preset=payload.preset,
            preset_options=payload.preset_options,
            input_url=payload.input_url,
            output_filename=payload.output_filename,
            webhook_url=payload.webhook_url,
            metadata_=payload.metadata,
            output_presigned_url=payload.output_presigned_url,
            status=JobStatus.QUEUED,
            stage="QUEUED",
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    # Enqueue Celery task
    from app.tasks import process_job
    process_job.delay(str(job_id))

    return JobCreated(job_id=job_id)


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID):
    """Get status and result of a job."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobResponse(
        job_id=job.id,
        status=job.status.value,
        stage=job.stage,
        preset=job.preset,
        output_url=job.output_url,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_seconds=job.duration_seconds,
        error=job.error_message,
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check: verifies API, Redis and PostgreSQL are reachable."""
    redis_ok = False
    postgres_ok = False

    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        redis_ok = True
    except Exception:
        pass

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(select(1))
        postgres_ok = True
    except Exception:
        pass

    overall = "ok" if (redis_ok and postgres_ok) else "degraded"
    return HealthResponse(status=overall, api=True, redis=redis_ok, postgres=postgres_ok)

# ── Command routes (Rendi-style raw FFmpeg) ───────────────────────────────────

@app.post("/v1/commands", response_model=CommandCreated, status_code=202)
@limiter.limit("20/minute")
async def create_command(request: Request, payload: CommandCreate):
    """Submit a raw FFmpeg command (Rendi-compatible API)."""
    # Validate all input URLs (SSRF protection)
    for alias, url in payload.input_files.items():
        try:
            validate_input_url(url)
        except URLSecurityError as e:
            raise HTTPException(status_code=400, detail=f"Unsafe URL for '{alias}': {e}")

    # Validate the FFmpeg command for security
    try:
        validate_ffmpeg_command(
            payload.ffmpeg_command,
            input_aliases=set(payload.input_files.keys()),
            output_aliases=set(payload.output_files.keys()),
        )
    except CommandSecurityError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async with AsyncSessionLocal() as db:
        cmd = Command(
            ffmpeg_command=payload.ffmpeg_command,
            input_files=payload.input_files,
            output_files_spec=payload.output_files,
            webhook_url=payload.webhook_url,
            output_presigned_urls=payload.output_presigned_urls,
            status=JobStatus.QUEUED,
            stage="QUEUED",
        )
        db.add(cmd)
        await db.commit()
        await db.refresh(cmd)
        cmd_id = cmd.id

    from app.tasks import process_command
    process_command.delay(str(cmd_id))

    return CommandCreated(command_id=cmd_id)


@app.get("/v1/commands/{command_id}", response_model=CommandResponse)
async def get_command(command_id: uuid.UUID):
    """Get status and result of a command."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Command).where(Command.id == command_id))
        cmd = result.scalar_one_or_none()

    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    output_files = {}
    for alias, data in (cmd.output_files_result or {}).items():
        output_files[alias] = OutputFileResult(
            url=data.get("url", ""),
            size_bytes=data.get("size_bytes"),
        )

    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status.value,
        stage=cmd.stage,
        ffmpeg_command=cmd.ffmpeg_command,
        input_files=cmd.input_files or {},
        output_files=output_files,
        created_at=cmd.created_at,
        started_at=cmd.started_at,
        finished_at=cmd.finished_at,
        duration_seconds=cmd.duration_seconds,
        error=cmd.error_message,
    )
