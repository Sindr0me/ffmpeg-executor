#!/usr/bin/env python3
"""
FFmpeg Executor MCP Server

Exposes the ffmpeg-executor API as MCP tools so Claude and other LLM clients
can submit FFmpeg jobs directly.
"""

import json
import os
import sys
import time
from typing import Any

import httpx

# MCP server using stdio transport (works with claude desktop, cursor, etc.)
API_BASE = os.environ.get("FFMPEG_API_BASE", "https://ffmpeg-api.kuprino.com")


def _call_api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    with httpx.Client(timeout=30) as client:
        if method == "GET":
            r = client.get(url)
        else:
            r = client.post(url, json=body)
        r.raise_for_status()
        return r.json()


def _poll_until_done(id_field: str, id_value: str, path_template: str, timeout: int = 300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _call_api("GET", path_template.format(id=id_value))
        status = result.get("status", "")
        if status in ("SUCCESS", "FAILED"):
            return result
        time.sleep(3)
    raise TimeoutError(f"Job did not complete within {timeout}s")


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "ffmpeg_run_command",
        "description": (
            "Run an arbitrary FFmpeg command via the ffmpeg-executor service. "
            "Input files are referenced as {{in_alias}} and output files as {{out_alias}} "
            "in the ffmpeg_command string. The service downloads inputs, runs FFmpeg, "
            "uploads outputs to R2 storage, and returns public URLs.\n\n"
            "Example ffmpeg_command: '-i {{in_video}} -vf scale=1280:720 -c:v libx264 -crf 23 {{out_result}}'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ffmpeg_command": {
                    "type": "string",
                    "description": "FFmpeg arguments string. Do NOT include the 'ffmpeg' word. "
                                   "Reference input files as {{in_alias}} and output files as {{out_alias}}."
                },
                "input_files": {
                    "type": "object",
                    "description": "Dict mapping input aliases (must start with 'in_') to HTTPS URLs. "
                                   "Example: {\"in_video\": \"https://example.com/video.mp4\"}",
                    "additionalProperties": {"type": "string"}
                },
                "output_files": {
                    "type": "object",
                    "description": "Dict mapping output aliases (must start with 'out_') to output filenames. "
                                   "Example: {\"out_result\": \"output.mp4\"}",
                    "additionalProperties": {"type": "string"}
                },
                "webhook_url": {
                    "type": "string",
                    "description": "Optional HTTPS webhook URL to POST results to on completion."
                },
                "wait": {
                    "type": "boolean",
                    "description": "If true (default), poll until the command finishes and return the full result. "
                                   "If false, return immediately with just the command_id.",
                    "default": True
                }
            },
            "required": ["ffmpeg_command", "input_files", "output_files"]
        }
    },
    {
        "name": "ffmpeg_run_preset",
        "description": (
            "Run a video processing preset job. Available presets:\n"
            "- transcode_h264_mp4: Convert to H.264 MP4 (options: crf, audio_bitrate)\n"
            "- scale_fit_max: Scale down to max dimensions (options: max_width, max_height, crf)\n"
            "- thumbnail_jpg: Extract frame as JPEG (options: at_seconds, quality)\n"
            "- burn_subs: Burn subtitles into video (options: input_subs_url, crf)\n"
            "- overlay_image: Overlay watermark/logo (options: input_overlay_url, x, y, crf)\n"
            "- extract_audio_mp3: Extract audio as MP3 (options: audio_bitrate)\n"
            "- concat_videos: Concatenate videos (options: input_urls list)\n"
            "- hls_package: Package as HLS ZIP (options: hls_time, video_bitrate)"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "preset": {"type": "string", "description": "Preset name"},
                "input_url": {"type": "string", "description": "HTTPS URL of the source video/audio"},
                "output_filename": {"type": "string", "description": "Output filename, e.g. 'result.mp4'"},
                "preset_options": {
                    "type": "object",
                    "description": "Preset-specific options dict",
                    "default": {}
                },
                "webhook_url": {"type": "string", "description": "Optional webhook URL"},
                "wait": {
                    "type": "boolean",
                    "description": "Poll until done (default true)",
                    "default": True
                }
            },
            "required": ["preset", "input_url", "output_filename"]
        }
    },
    {
        "name": "ffmpeg_get_command",
        "description": "Get the status and result of a previously submitted FFmpeg command.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command_id": {"type": "string", "description": "Command UUID from ffmpeg_run_command"}
            },
            "required": ["command_id"]
        }
    },
    {
        "name": "ffmpeg_get_job",
        "description": "Get the status and result of a previously submitted preset job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job UUID from ffmpeg_run_preset"}
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "ffmpeg_health",
        "description": "Check the health of the ffmpeg-executor service.",
        "inputSchema": {"type": "object", "properties": {}}
    }
]


# ── MCP stdio protocol ────────────────────────────────────────────────────────

def send(obj: dict) -> None:
    line = json.dumps(obj)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def handle_call_tool(name: str, args: dict) -> Any:
    if name == "ffmpeg_health":
        return _call_api("GET", "/health")

    elif name == "ffmpeg_run_command":
        wait = args.pop("wait", True)
        resp = _call_api("POST", "/v1/commands", args)
        if not wait:
            return resp
        cmd_id = resp["command_id"]
        return _poll_until_done("command_id", cmd_id, "/v1/commands/{id}")

    elif name == "ffmpeg_run_preset":
        wait = args.pop("wait", True)
        resp = _call_api("POST", "/jobs", args)
        if not wait:
            return resp
        job_id = resp["job_id"]
        return _poll_until_done("job_id", job_id, "/jobs/{id}")

    elif name == "ffmpeg_get_command":
        return _call_api("GET", f"/v1/commands/{args['command_id']}")

    elif name == "ffmpeg_get_job":
        return _call_api("GET", f"/jobs/{args['job_id']}")

    else:
        raise ValueError(f"Unknown tool: {name}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")

        if method == "initialize":
            send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ffmpeg-executor", "version": "1.0.0"}
                }
            })

        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})

        elif method == "tools/call":
            tool_name = msg["params"]["name"]
            tool_args = msg["params"].get("arguments", {})
            try:
                result = handle_call_tool(tool_name, dict(tool_args))
                send({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
                    }
                })
            except Exception as e:
                send({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True
                    }
                })

        elif method == "notifications/initialized":
            pass  # no response needed

        else:
            if msg_id is not None:
                send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
