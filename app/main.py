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
from app.models import Base, Job, JobStatus
from app.schemas import HealthResponse, JobCreate, JobCreated, JobResponse
from app.security import URLSecurityError, validate_input_url

settings = get_settings()

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/jobs", response_model=JobCreated, status_code=202)
async def create_job(payload: JobCreate):
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
