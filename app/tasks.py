"""Celery tasks: download → ffmpeg → upload → webhook."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone

import httpx
import requests
from celery import Celery
from celery.utils.log import get_task_logger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import Job, JobStatus
from app.presets import get_preset
from app.security import validate_input_url
from app.storage import upload_file, upload_to_presigned_url

settings = get_settings()

celery_app = Celery(
    "ffmpeg_executor",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

logger = get_task_logger(__name__)

# Sync engine for Celery workers
_engine = create_engine(
    settings.database_url.replace("+asyncpg", ""),
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=_engine)

# ─── helpers ─────────────────────────────────────────────────────────────────

def _set_status(db, job: Job, status: JobStatus, stage: str | None = None):
    job.status = status
    if stage:
        job.stage = stage
    if status in (JobStatus.DOWNLOADING, JobStatus.PROCESSING, JobStatus.UPLOADING):
        if not job.started_at:
            job.started_at = datetime.now(timezone.utc)
    if status in (JobStatus.SUCCESS, JobStatus.FAILED):
        job.finished_at = datetime.now(timezone.utc)
    db.commit()


MAX_INPUT_BYTES = 500 * 1024 * 1024  # 500 MB hard limit

def _download_file(url: str, dest_path: str, max_retries: int = 3) -> None:
    validate_input_url(url)
    backoffs = [10, 30, 90]
    import time
    for attempt in range(max_retries):
        try:
            with httpx.stream("GET", url, follow_redirects=True,
                              timeout=httpx.Timeout(connect=15, read=120, write=30, pool=5)) as r:
                r.raise_for_status()
                # Reject files that are too large based on Content-Length header
                content_length = r.headers.get("content-length")
                if content_length and int(content_length) > MAX_INPUT_BYTES:
                    raise RuntimeError(
                        f"Input file too large: {int(content_length) // (1024*1024)} MB "
                        f"(limit: {MAX_INPUT_BYTES // (1024*1024)} MB)"
                    )
                downloaded = 0
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > MAX_INPUT_BYTES:
                            raise RuntimeError(
                                f"Input file exceeds {MAX_INPUT_BYTES // (1024*1024)} MB limit "
                                f"(stopped at {downloaded // (1024*1024)} MB)"
                            )
                        f.write(chunk)
            return
        except RuntimeError:
            raise  # don't retry size errors
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(backoffs[attempt])
            else:
                raise RuntimeError(f"Download failed after {max_retries} attempts: {e}") from e


def _run_ffmpeg(args: list[str], timeout: int) -> str:
    """Run ffmpeg with the given args. Returns stderr (last 8KB)."""
    cmd = ["ffmpeg"] + args
    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        stderr_tail = result.stderr[-8192:] if result.stderr else ""
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg exited with code {result.returncode}. stderr:\n{stderr_tail}"
            )
        return stderr_tail
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg exceeded timeout of {timeout}s")


def _send_webhook(webhook_url: str, payload: dict, max_retries: int = 5) -> None:
    import time
    backoffs = [5, 15, 30, 60, 120]
    for attempt in range(max_retries):
        try:
            r = requests.post(webhook_url, json=payload, timeout=15)
            r.raise_for_status()
            return
        except Exception as e:
            logger.warning("Webhook attempt %d failed: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(backoffs[attempt])


# ─── main task ───────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=0)
def process_job(self, job_id: str) -> None:
    db = SessionLocal()
    work_dir = None
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error("Job %s not found", job_id)
            return

        work_dir = tempfile.mkdtemp(dir=settings.ffmpeg_work_dir, prefix=f"job_{job_id}_")
        preset_def = get_preset(job.preset)
        options = {**preset_def.defaults, **(job.preset_options or {})}

        # ── DOWNLOAD PHASE ────────────────────────────────────────────────
        _set_status(db, job, JobStatus.DOWNLOADING, "DOWNLOADING")

        # Detect extension from output_filename or URL
        ext = job.input_url.rsplit(".", 1)[-1].split("?")[0] or "bin"
        input_path = os.path.join(work_dir, f"input.{ext}")
        _download_file(job.input_url, input_path)

        # Download extra inputs (subs, overlay, etc.)
        extra_inputs: dict[str, str] = {}
        for field_name in preset_def.extra_input_fields:
            if field_name == "input_urls":
                # concat_videos: list of URLs
                urls: list[str] = options.get("input_urls", [])
                concat_list_path = os.path.join(work_dir, "concat.txt")
                paths = []
                for idx, url in enumerate(urls):
                    ext_i = url.rsplit(".", 1)[-1].split("?")[0] or "bin"
                    p = os.path.join(work_dir, f"concat_{idx}.{ext_i}")
                    _download_file(url, p)
                    paths.append(p)
                with open(concat_list_path, "w") as f:
                    for p in paths:
                        f.write(f"file {p}\n")
                input_path = concat_list_path  # override primary input
            else:
                url = options.get(field_name, "")
                if url:
                    validate_input_url(url)
                    extra_ext = url.rsplit(".", 1)[-1].split("?")[0] or "bin"
                    p = os.path.join(work_dir, f"extra_{field_name}.{extra_ext}")
                    _download_file(url, p)
                    extra_inputs[field_name] = p

        # ── PROCESSING PHASE ──────────────────────────────────────────────
        _set_status(db, job, JobStatus.PROCESSING, "PROCESSING")

        output_path = os.path.join(work_dir, job.output_filename)

        # Special case: hls_package produces a directory, we zip it
        is_hls = job.preset == "hls_package"
        if is_hls:
            output_path = os.path.join(work_dir, "placeholder.mp4")  # not used

        ffmpeg_args = preset_def.build_cmd(
            input_path=input_path,
            output_path=output_path,
            options=options,
            extra_inputs=extra_inputs,
            work_dir=work_dir,
        )

        stderr_tail = _run_ffmpeg(ffmpeg_args, timeout=settings.ffmpeg_max_run_seconds)
        job.ffmpeg_stderr = stderr_tail

        # For HLS: zip the output directory
        if is_hls:
            hls_dir = os.path.join(work_dir, "hls")
            zip_path = os.path.join(work_dir, job.output_filename)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname in os.listdir(hls_dir):
                    zf.write(os.path.join(hls_dir, fname), fname)
            output_path = zip_path

        # ── UPLOAD PHASE ──────────────────────────────────────────────────
        _set_status(db, job, JobStatus.UPLOADING, "UPLOADING")

        if job.output_presigned_url:
            # Upload directly to caller-supplied S3 presigned URL
            logger.info("Job %s: uploading via presigned URL", job_id)
            output_url = upload_to_presigned_url(output_path, job.output_presigned_url)
        else:
            output_url = upload_file(output_path, str(job.id), job.output_filename)

        job.output_url = output_url
        _set_status(db, job, JobStatus.SUCCESS, "DONE")

        logger.info("Job %s SUCCESS → %s", job_id, output_url)

        # ── WEBHOOK ───────────────────────────────────────────────────────
        if job.webhook_url:
            _send_webhook(job.webhook_url, {
                "job_id": str(job.id),
                "status": "SUCCESS",
                "output_url": output_url,
                "duration_seconds": job.duration_seconds,
                "metadata": job.metadata_ or {},
            })

    except Exception as exc:
        logger.exception("Job %s FAILED: %s", job_id, exc)
        if db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(exc)[:2000]
                job.finished_at = datetime.now(timezone.utc)
                db.commit()

                if job.webhook_url:
                    try:
                        _send_webhook(job.webhook_url, {
                            "job_id": str(job.id),
                            "status": "FAILED",
                            "output_url": None,
                            "duration_seconds": job.duration_seconds,
                            "metadata": job.metadata_ or {},
                            "error": str(exc)[:500],
                        })
                    except Exception:
                        pass
    finally:
        db.close()
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

@celery_app.task(bind=True, max_retries=0)
def process_command(self, command_id: str) -> None:
    from app.models import Command
    from app.security import validate_ffmpeg_command, PLACEHOLDER_RE

    db = SessionLocal()
    work_dir = None
    try:
        cmd_obj = db.query(Command).filter(Command.id == command_id).first()
        if not cmd_obj:
            logger.error("Command %s not found", command_id)
            return

        work_dir = tempfile.mkdtemp(
            dir=settings.ffmpeg_work_dir,
            prefix=f"cmd_{command_id}_"
        )

        # ── DOWNLOAD PHASE ────────────────────────────────────────────────
        _set_status(db, cmd_obj, JobStatus.DOWNLOADING, "DOWNLOADING")

        alias_to_path: dict[str, str] = {}

        for alias, url in (cmd_obj.input_files or {}).items():
            ext = url.rsplit(".", 1)[-1].split("?")[0][:8] or "bin"
            local_path = os.path.join(work_dir, f"{alias}.{ext}")
            _download_file(url, local_path)
            alias_to_path[alias] = local_path
            logger.info("Downloaded %s → %s", alias, local_path)

        for alias, filename in (cmd_obj.output_files_spec or {}).items():
            local_path = os.path.join(work_dir, filename)
            alias_to_path[alias] = local_path

        # ── PROCESSING PHASE ──────────────────────────────────────────────
        _set_status(db, cmd_obj, JobStatus.PROCESSING, "PROCESSING")

        # Resolve placeholders in command
        def replace_alias(m):
            a = m.group(1)
            if a not in alias_to_path:
                raise RuntimeError(f"Alias {a} not resolved")
            return alias_to_path[a]

        resolved_cmd = PLACEHOLDER_RE.sub(replace_alias, cmd_obj.ffmpeg_command)

        # Build ffmpeg args — add safety flags at the front
        import shlex
        raw_args = shlex.split(resolved_cmd)
        ffmpeg_args = ["-nostdin", "-protocol_whitelist", "file,pipe"] + raw_args

        stderr_tail = _run_ffmpeg(ffmpeg_args, timeout=settings.ffmpeg_max_run_seconds)
        cmd_obj.ffmpeg_stderr = stderr_tail

        # ── UPLOAD PHASE ──────────────────────────────────────────────────
        _set_status(db, cmd_obj, JobStatus.UPLOADING, "UPLOADING")

        presigned_map = cmd_obj.output_presigned_urls or {}
        output_results: dict[str, dict] = {}

        for alias, filename in (cmd_obj.output_files_spec or {}).items():
            local_path = alias_to_path[alias]
            if not os.path.exists(local_path):
                raise RuntimeError(f"Output file for alias {alias} was not created: {filename}")
            size_bytes = os.path.getsize(local_path)

            if alias in presigned_map:
                # Upload to caller-supplied presigned URL for this alias
                logger.info("Command %s: uploading %s via presigned URL", command_id, alias)
                pub_url = upload_to_presigned_url(local_path, presigned_map[alias])
            else:
                pub_url = upload_file(local_path, command_id, filename)

            output_results[alias] = {"url": pub_url, "size_bytes": size_bytes}
            logger.info("Uploaded %s → %s", alias, pub_url)

        cmd_obj.output_files_result = output_results
        _set_status(db, cmd_obj, JobStatus.SUCCESS, "DONE")
        logger.info("Command %s SUCCESS", command_id)

        if cmd_obj.webhook_url:
            _send_webhook(cmd_obj.webhook_url, {
                "command_id": str(cmd_obj.id),
                "status": "SUCCESS",
                "output_files": output_results,
                "duration_seconds": cmd_obj.duration_seconds,
            })

    except Exception as exc:
        logger.exception("Command %s FAILED: %s", command_id, exc)
        if db:
            cmd_obj = db.query(Command).filter(Command.id == command_id).first()
            if cmd_obj:
                cmd_obj.status = JobStatus.FAILED
                cmd_obj.error_message = str(exc)[:2000]
                cmd_obj.finished_at = datetime.now(timezone.utc)
                db.commit()

                if cmd_obj.webhook_url:
                    try:
                        _send_webhook(cmd_obj.webhook_url, {
                            "command_id": str(cmd_obj.id),
                            "status": "FAILED",
                            "output_files": {},
                            "error": str(exc)[:500],
                        })
                    except Exception:
                        pass
    finally:
        db.close()
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
