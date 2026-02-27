"""
FFmpeg preset definitions.

Each preset is a function that receives:
  - input_path: str — path to the primary input file
  - output_path: str — path for the output file
  - options: dict — preset-specific parameters (already validated / defaulted)

Returns: list[str] — the complete ffmpeg argv (without the 'ffmpeg' binary itself).

Extra input files (subs, overlay, concat list) are handled per-preset.
"""

from __future__ import annotations
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PresetDef:
    name: str
    description: str
    extra_input_fields: list[str]  # option keys that are additional input URLs
    build_cmd: Callable[..., list[str]]
    defaults: dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────────
# 1. transcode_h264_mp4
# ────────────────────────────────────────────────────────────────────────────

def _transcode_h264(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    crf = int(options.get("crf", 23))
    audio_bitrate = options.get("audio_bitrate", "128k")
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 2. scale_fit_max
# ────────────────────────────────────────────────────────────────────────────

def _scale_fit_max(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    max_width = int(options.get("max_width", 1920))
    max_height = int(options.get("max_height", 1080))
    crf = int(options.get("crf", 23))
    vf = f"scale='min({max_width},iw)':'min({max_height},ih)':force_original_aspect_ratio=decrease"
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 3. thumbnail_jpg
# ────────────────────────────────────────────────────────────────────────────

def _thumbnail_jpg(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    at_seconds = float(options.get("at_seconds", 1))
    quality = int(options.get("quality", 5))
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-ss", str(at_seconds),
        "-i", input_path,
        "-frames:v", "1",
        "-q:v", str(quality),
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 4. burn_subs
# ────────────────────────────────────────────────────────────────────────────

def _burn_subs(input_path: str, output_path: str, options: dict, extra_inputs: dict, **_) -> list[str]:
    subs_path = extra_inputs.get("input_subs_url", "")
    crf = int(options.get("crf", 23))
    # Use subtitles filter; need to escape path for libass
    escaped = subs_path.replace("\\", "/").replace(":", "\\:")
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-vf", f"subtitles='{escaped}'",
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 5. overlay_image
# ────────────────────────────────────────────────────────────────────────────

def _overlay_image(input_path: str, output_path: str, options: dict, extra_inputs: dict, **_) -> list[str]:
    overlay_path = extra_inputs.get("input_overlay_url", "")
    x = options.get("x", 10)
    y = options.get("y", 10)
    crf = int(options.get("crf", 23))
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-i", overlay_path,
        "-filter_complex", f"overlay={x}:{y}",
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 6. extract_audio_mp3
# ────────────────────────────────────────────────────────────────────────────

def _extract_audio_mp3(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    audio_bitrate = options.get("audio_bitrate", "128k")
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", audio_bitrate,
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 7. concat_videos — handled specially in tasks.py (writes concat list file)
# ────────────────────────────────────────────────────────────────────────────

def _concat_videos(input_path: str, output_path: str, options: dict, extra_inputs: dict, **_) -> list[str]:
    # input_path is the concat list file written by tasks.py
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-f", "concat",
        "-safe", "0",
        "-i", input_path,
        "-c", "copy",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 8. hls_package — output is a .zip of m3u8 + segments
# ────────────────────────────────────────────────────────────────────────────

def _hls_package(input_path: str, output_path: str, options: dict, work_dir: str, **_) -> list[str]:
    hls_time = int(options.get("hls_time", 6))
    video_bitrate = options.get("video_bitrate", "1000k")
    hls_dir = os.path.join(work_dir, "hls")
    os.makedirs(hls_dir, exist_ok=True)
    m3u8_path = os.path.join(hls_dir, "index.m3u8")
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-c:v", "libx264",
        "-b:v", video_bitrate,
        "-c:a", "aac",
        "-hls_time", str(hls_time),
        "-hls_list_size", "0",
        "-hls_segment_filename", os.path.join(hls_dir, "seg%03d.ts"),
        "-f", "hls",
        "-y", m3u8_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────────────────

PRESETS: dict[str, PresetDef] = {
    "transcode_h264_mp4": PresetDef(
        name="transcode_h264_mp4",
        description="Convert video to H.264 + AAC MP4",
        extra_input_fields=[],
        build_cmd=_transcode_h264,
        defaults={"crf": 23, "audio_bitrate": "128k"},
    ),
    "scale_fit_max": PresetDef(
        name="scale_fit_max",
        description="Scale video down to fit within max dimensions",
        extra_input_fields=[],
        build_cmd=_scale_fit_max,
        defaults={"max_width": 1920, "max_height": 1080, "crf": 23},
    ),
    "thumbnail_jpg": PresetDef(
        name="thumbnail_jpg",
        description="Extract a single frame as JPEG",
        extra_input_fields=[],
        build_cmd=_thumbnail_jpg,
        defaults={"at_seconds": 1, "quality": 5},
    ),
    "burn_subs": PresetDef(
        name="burn_subs",
        description="Burn subtitles (SRT/ASS) into video",
        extra_input_fields=["input_subs_url"],
        build_cmd=_burn_subs,
        defaults={"crf": 23},
    ),
    "overlay_image": PresetDef(
        name="overlay_image",
        description="Overlay a PNG/JPG watermark on video",
        extra_input_fields=["input_overlay_url"],
        build_cmd=_overlay_image,
        defaults={"x": 10, "y": 10, "crf": 23},
    ),
    "extract_audio_mp3": PresetDef(
        name="extract_audio_mp3",
        description="Extract audio track as MP3",
        extra_input_fields=[],
        build_cmd=_extract_audio_mp3,
        defaults={"audio_bitrate": "128k"},
    ),
    "concat_videos": PresetDef(
        name="concat_videos",
        description="Concatenate multiple videos (stream copy)",
        extra_input_fields=["input_urls"],  # list of URLs in options
        build_cmd=_concat_videos,
        defaults={},
    ),
    "hls_package": PresetDef(
        name="hls_package",
        description="Package video as HLS (m3u8 + segments), output as ZIP",
        extra_input_fields=[],
        build_cmd=_hls_package,
        defaults={"hls_time": 6, "video_bitrate": "1000k"},
    ),
}


def get_preset(name: str) -> PresetDef:
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: '{name}'. Available: {list(PRESETS.keys())}")
    return PRESETS[name]
