#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp[cli]>=1.0.0",
#   "httpx>=0.27.0",
#   "pydantic>=2.0.0",
# ]
# ///
"""
MCP Server for FFmpeg Executor API.

Exposes the ffmpeg-executor service as MCP tools so Claude and other LLM clients
can submit arbitrary FFmpeg commands and preset-based video processing jobs.

API base: https://ffmpeg-api.kuprino.com
"""

import json
import os
import sys
import time
from enum import Enum
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = os.environ.get("FFMPEG_API_BASE", "https://ffmpeg-api.kuprino.com")
DEFAULT_POLL_TIMEOUT = 300  # seconds

mcp = FastMCP("ffmpeg_executor_mcp")


# ── Shared API client ─────────────────────────────────────────────────────────

async def _api_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API_BASE}{path}")
        r.raise_for_status()
        return r.json()


async def _api_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{API_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()


def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        try:
            detail = e.response.json().get("detail", e.response.text)
        except Exception:
            detail = e.response.text
        if code == 400:
            return f"Error 400 – Bad request: {detail}"
        if code == 404:
            return f"Error 404 – Not found: {detail}"
        if code == 422:
            return f"Error 422 – Validation error: {detail}"
        return f"Error {code}: {detail}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. The service may be overloaded — try again."
    return f"Error: {type(e).__name__}: {e}"


async def _poll(path_template: str, id_value: str, timeout: int) -> dict:
    """Poll an endpoint until status is SUCCESS or FAILED."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = await _api_get(path_template.format(id=id_value))
        status = result.get("status", "")
        if status in ("SUCCESS", "FAILED"):
            return result
        await _sleep_async(3)
    raise TimeoutError(f"Did not complete within {timeout}s")


async def _sleep_async(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


# ── Input models ──────────────────────────────────────────────────────────────

class RunCommandInput(BaseModel):
    """Input for raw FFmpeg command execution."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    ffmpeg_command: str = Field(
        ...,
        description=(
            "FFmpeg arguments string. Do NOT include the 'ffmpeg' word. "
            "Reference input files as {{in_alias}} and outputs as {{out_alias}}. "
            "Example: '-i {{in_video}} -vf scale=1280:720 -c:v libx264 -crf 23 {{out_720p}}'"
        ),
        min_length=5,
    )
    input_files: dict[str, str] = Field(
        ...,
        description=(
            "Map of input aliases to HTTPS source URLs. "
            "Keys MUST start with 'in_'. "
            "Example: {\"in_video\": \"https://example.com/video.mp4\"}"
        ),
    )
    output_files: dict[str, str] = Field(
        ...,
        description=(
            "Map of output aliases to output filenames. "
            "Keys MUST start with 'out_'. "
            "Example: {\"out_result\": \"output.mp4\", \"out_thumb\": \"thumb.jpg\"}"
        ),
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="Optional HTTPS URL to POST the result to when the command finishes.",
    )
    wait: bool = Field(
        default=True,
        description="If true (default), poll until done and return the full result. "
                    "If false, return immediately with just the command_id.",
    )
    timeout: int = Field(
        default=DEFAULT_POLL_TIMEOUT,
        description="Max seconds to wait when wait=true (default 300).",
        ge=10,
        le=3600,
    )

    @field_validator("input_files")
    @classmethod
    def validate_input_aliases(cls, v: dict) -> dict:
        for key in v:
            if not key.startswith("in_"):
                raise ValueError(f"input_files keys must start with 'in_', got: '{key}'")
        return v

    @field_validator("output_files")
    @classmethod
    def validate_output_aliases(cls, v: dict) -> dict:
        for key in v:
            if not key.startswith("out_"):
                raise ValueError(f"output_files keys must start with 'out_', got: '{key}'")
        return v


class RunPresetInput(BaseModel):
    """Input for preset-based job submission."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    preset: str = Field(
        ...,
        description=(
            "Preset name. Available:\n"
            "  BASIC: transcode_h264_mp4, scale_fit_max, thumbnail_jpg, extract_audio_mp3\n"
            "  OVERLAYS: burn_subs, overlay_image, add_text\n"
            "  SOCIAL/UGC: crop_to_aspect, trim, speed_change, fade, gif_export\n"
            "  AUDIO: normalize_loudness, mix_audio\n"
            "  DELIVERY: concat_videos, hls_package"
        ),
    )
    input_url: str = Field(
        ...,
        description="HTTPS URL of the source video or audio file.",
    )
    output_filename: str = Field(
        ...,
        description="Filename for the result, e.g. 'output.mp4' or 'thumb.jpg'.",
        min_length=3,
    )
    preset_options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Preset-specific options. Examples:\n"
            "  thumbnail_jpg: {at_seconds: 5.0, quality: 3}\n"
            "  transcode_h264_mp4: {crf: 20, audio_bitrate: '192k'}\n"
            "  scale_fit_max: {max_width: 1280, max_height: 720}\n"
            "  burn_subs: {input_subs_url: 'https://...'}\n"
            "  overlay_image: {input_overlay_url: 'https://...', x: 10, y: 10}\n"
            "  extract_audio_mp3: {audio_bitrate: '128k'}\n"
            "  concat_videos: {input_urls: ['https://...', 'https://...']}\n"
            "  --- UGC / Social Media ---\n"
            "  crop_to_aspect: {aspect_ratio: '9:16'}  # also: '4:5', '1:1', '16:9'\n"
            "  trim: {start_time: '00:00:05', end_time: '00:00:30'}\n"
            "  normalize_loudness: {target_lufs: -14}  # -14 YouTube, -16 TikTok/Meta\n"
            "  speed_change: {speed: 1.5}  # 0.5=slowmo, 2.0=double speed\n"
            "  mix_audio: {bg_music_url: 'https://...', bg_volume: 0.15, main_volume: 1.0}\n"
            "  fade: {fade_in_duration: 0.5, fade_out_duration: 0.5, fade_out_start: 25.0}\n"
            "  add_text: {text: 'Shop Now!', y: 'h-th-40', fontsize: 52, fontcolor: 'white'}\n"
            "  gif_export: {fps: 15, width: 480, start_time: 0, duration: 5}\n"
            "  hls_package: {hls_time: 6, video_bitrate: '1000k'}"
        ),
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="Optional HTTPS webhook URL for result delivery.",
    )
    wait: bool = Field(
        default=True,
        description="Poll until done (default true).",
    )
    timeout: int = Field(
        default=DEFAULT_POLL_TIMEOUT,
        description="Max wait seconds (default 300).",
        ge=10,
        le=3600,
    )


class GetCommandInput(BaseModel):
    """Input for polling a command by ID."""
    model_config = ConfigDict(str_strip_whitespace=True)

    command_id: str = Field(..., description="Command UUID from ffmpeg_run_command.")


class GetJobInput(BaseModel):
    """Input for polling a preset job by ID."""
    model_config = ConfigDict(str_strip_whitespace=True)

    job_id: str = Field(..., description="Job UUID from ffmpeg_run_preset.")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="ffmpeg_run_command",
    annotations={
        "title": "Run Raw FFmpeg Command",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ffmpeg_run_command(params: RunCommandInput) -> str:
    """Run an arbitrary FFmpeg command via the ffmpeg-executor service.

    Downloads all input_files, resolves {{in_alias}} / {{out_alias}} placeholders
    in ffmpeg_command to real paths, runs FFmpeg, uploads all outputs to Cloudflare R2,
    and returns public URLs for each output file.

    Supports multiple output files in a single run (e.g. thumbnail + 480p video).
    By default (wait=true), polls until done and returns the complete result.

    Args:
        params (RunCommandInput): Validated input containing:
            - ffmpeg_command (str): FFmpeg args with {{in_alias}}/{{out_alias}} placeholders
            - input_files (dict): {"in_alias": "https://..."} — keys must start with 'in_'
            - output_files (dict): {"out_alias": "filename.ext"} — keys must start with 'out_'
            - webhook_url (Optional[str]): HTTPS callback on completion
            - wait (bool): Poll until done, default true
            - timeout (int): Max wait seconds, default 300

    Returns:
        str: JSON with command_id, status, output_files (each with url + size_bytes),
             duration_seconds. On FAILED: includes error message.

    Examples:
        - Thumbnail: ffmpeg_command="-i {{in_v}} -ss 3 -frames:v 1 -q:v 2 {{out_thumb}}"
        - Transcode:  ffmpeg_command="-i {{in_v}} -vf scale=1280:720 -c:v libx264 -crf 23 {{out_720}}"
        - Extract audio: ffmpeg_command="-i {{in_v}} -vn -c:a libmp3lame -b:a 128k {{out_mp3}}"
        - Multi-output: generate thumb + 480p in a single call

    Error Handling:
        - 400: Blocked FFmpeg pattern or invalid alias format
        - Returns error string on network/timeout issues
    """
    try:
        body: dict[str, Any] = {
            "ffmpeg_command": params.ffmpeg_command,
            "input_files": params.input_files,
            "output_files": params.output_files,
        }
        if params.webhook_url:
            body["webhook_url"] = params.webhook_url

        resp = await _api_post("/v1/commands", body)
        command_id = resp["command_id"]

        if not params.wait:
            return json.dumps({"command_id": command_id}, indent=2)

        result = await _poll("/v1/commands/{id}", command_id, params.timeout)
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="ffmpeg_run_preset",
    annotations={
        "title": "Run FFmpeg Preset Job",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ffmpeg_run_preset(params: RunPresetInput) -> str:
    """Run a video processing preset job on the ffmpeg-executor service.

    Available presets:

    BASIC:
    - transcode_h264_mp4: Convert to H.264 + AAC MP4. Options: crf (23), audio_bitrate ('128k')
    - scale_fit_max: Scale down to max dimensions. Options: max_width (1920), max_height (1080), crf (23)
    - thumbnail_jpg: Extract frame as JPEG. Options: at_seconds (1.0), quality (5, lower=better)
    - extract_audio_mp3: Extract audio as MP3. Options: audio_bitrate ('128k')

    OVERLAYS:
    - burn_subs: Burn subtitles into video. Options: input_subs_url (required), crf (23)
    - overlay_image: Watermark/logo overlay. Options: input_overlay_url (required), x (10), y (10), crf (23)
    - add_text: Burn text caption/CTA. Options: text, x, y, fontsize (52), fontcolor ('white'), box (true), start_time, end_time

    SOCIAL MEDIA / UGC:
    - crop_to_aspect: Center-crop to aspect ratio. Options: aspect_ratio ('9:16'|'4:5'|'1:1'|'16:9'), crf (23)
    - trim: Cut clip by time. Options: start_time ('0'), end_time OR duration (seconds or HH:MM:SS)
    - speed_change: Change speed. Options: speed (1.5), keep_audio ('true'), crf (23). 0.5=slowmo, 2.0=fast
    - fade: Fade in/out. Options: fade_in_duration (0.5), fade_out_duration (0.5), fade_out_start (seconds), audio_fade ('true')
    - gif_export: Export as GIF. Options: fps (15), width (480), start_time (0), duration (5)

    AUDIO:
    - normalize_loudness: EBU R128 normalization. Options: target_lufs (-14.0), target_lra (7.0), target_tp (-1.0)
    - mix_audio: Add background music. Options: bg_music_url (required), bg_volume (0.15), main_volume (1.0)

    DELIVERY:
    - concat_videos: Join multiple videos. Options: input_urls (required list)
    - hls_package: Package as HLS ZIP. Options: hls_time (6), video_bitrate ('1000k')

    Args:
        params (RunPresetInput): Validated input containing:
            - preset (str): Preset name
            - input_url (str): HTTPS source URL
            - output_filename (str): e.g. 'result.mp4'
            - preset_options (dict): Preset-specific options
            - webhook_url (Optional[str]): Callback URL
            - wait (bool): Poll until done, default true
            - timeout (int): Max wait seconds

    Returns:
        str: JSON with job_id, status, output_url (public URL), duration_seconds.

    Examples:
        - Thumbnail at 5s: preset='thumbnail_jpg', preset_options={'at_seconds': 5.0}
        - 720p transcode: preset='transcode_h264_mp4', preset_options={'crf': 20}
        - Extract audio: preset='extract_audio_mp3', output_filename='audio.mp3'

    Error Handling:
        - 400: Unknown preset or unsafe URL
        - Returns error string on network issues
    """
    try:
        body: dict[str, Any] = {
            "preset": params.preset,
            "input_url": params.input_url,
            "output_filename": params.output_filename,
            "preset_options": params.preset_options,
        }
        if params.webhook_url:
            body["webhook_url"] = params.webhook_url

        resp = await _api_post("/jobs", body)
        job_id = resp["job_id"]

        if not params.wait:
            return json.dumps({"job_id": job_id}, indent=2)

        result = await _poll("/jobs/{id}", job_id, params.timeout)
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="ffmpeg_get_command",
    annotations={
        "title": "Get FFmpeg Command Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ffmpeg_get_command(params: GetCommandInput) -> str:
    """Get the current status and result of a previously submitted FFmpeg command.

    Use this to check on a command submitted with wait=false, or to re-fetch
    the output URLs of a completed command.

    Args:
        params (GetCommandInput):
            - command_id (str): UUID from ffmpeg_run_command

    Returns:
        str: JSON with command_id, status (QUEUED/DOWNLOADING/PROCESSING/UPLOADING/SUCCESS/FAILED),
             ffmpeg_command, input_files, output_files (with url + size_bytes per alias),
             duration_seconds, error (on FAILED).

    Error Handling:
        - 404: command_id not found
    """
    try:
        result = await _api_get(f"/v1/commands/{params.command_id}")
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="ffmpeg_get_job",
    annotations={
        "title": "Get Preset Job Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ffmpeg_get_job(params: GetJobInput) -> str:
    """Get the current status and result of a previously submitted preset job.

    Args:
        params (GetJobInput):
            - job_id (str): UUID from ffmpeg_run_preset

    Returns:
        str: JSON with job_id, status, preset, output_url (public URL on SUCCESS),
             duration_seconds, error (on FAILED).

    Error Handling:
        - 404: job_id not found
    """
    try:
        result = await _api_get(f"/jobs/{params.job_id}")
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="ffmpeg_health",
    annotations={
        "title": "Check FFmpeg Service Health",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ffmpeg_health() -> str:
    """Check whether the ffmpeg-executor service and all its components are healthy.

    Returns:
        str: JSON with status ('ok' or 'degraded'), api (bool), redis (bool), postgres (bool).

    Examples:
        - Use before submitting jobs to verify service is up
        - Use to diagnose why jobs are failing or queuing
    """
    try:
        result = await _api_get("/health")
        return json.dumps(result, indent=2)
    except Exception as e:
        return _handle_error(e)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
