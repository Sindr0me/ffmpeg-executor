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
# 7. concat_videos
# ────────────────────────────────────────────────────────────────────────────

def _concat_videos(input_path: str, output_path: str, options: dict, extra_inputs: dict, **_) -> list[str]:
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
# 8. hls_package
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


# ════════════════════════════════════════════════════════════════════════════
# UGC / Social Media Presets
# ════════════════════════════════════════════════════════════════════════════


# ────────────────────────────────────────────────────────────────────────────
# 9. crop_to_aspect — Center-crop to target aspect ratio
# ────────────────────────────────────────────────────────────────────────────

def _crop_to_aspect(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    ratio = options.get("aspect_ratio", "9:16")
    w_r, h_r = (int(x) for x in ratio.split(":"))
    crf = int(options.get("crf", 23))

    # Center-crop to the largest rectangle of target ratio that fits in source.
    # min(iw, ih*W/H) selects width: if source is wider than target, crop width.
    # min(ih, iw*H/W) selects height: if source is taller than target, crop height.
    # Ensure even dimensions with trunc()/2*2 for H.264 compatibility.
    crop_filter = (
        f"crop=min(iw\\,ih*{w_r}/{h_r}):min(ih\\,iw*{h_r}/{w_r})"
        f":(iw-min(iw\\,ih*{w_r}/{h_r}))/2"
        f":(ih-min(ih\\,iw*{h_r}/{w_r}))/2,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-vf", crop_filter,
        "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 10. trim — Cut video to time range
# ────────────────────────────────────────────────────────────────────────────

def _trim(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    start = str(options.get("start_time", "0"))
    duration = options.get("duration")
    end = options.get("end_time")

    args = ["-nostdin", "-protocol_whitelist", "file,pipe"]
    # -ss before -i: fast key-frame seek
    if start and start != "0":
        args += ["-ss", start]
    args += ["-i", input_path]
    if duration:
        args += ["-t", str(duration)]
    elif end:
        args += ["-to", str(end)]
    args += ["-c", "copy", "-y", output_path]
    return args


# ────────────────────────────────────────────────────────────────────────────
# 11. normalize_loudness — EBU R128 loudness normalization
# ────────────────────────────────────────────────────────────────────────────

def _normalize_loudness(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    target_lufs = float(options.get("target_lufs", -14.0))
    target_lra  = float(options.get("target_lra", 7.0))
    target_tp   = float(options.get("target_tp", -1.0))

    loudnorm = f"loudnorm=I={target_lufs}:LRA={target_lra}:TP={target_tp}"
    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-af", loudnorm,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 12. speed_change — Change playback speed
# ────────────────────────────────────────────────────────────────────────────

def _atempo_chain(speed: float) -> str:
    """Build chained atempo filters. FFmpeg atempo range is [0.5, 2.0]."""
    filters = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}")
    return ",".join(filters)


def _speed_change(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    speed = float(options.get("speed", 1.5))
    crf = int(options.get("crf", 23))
    keep_audio = str(options.get("keep_audio", "true")).lower() != "false"

    pts = 1.0 / speed
    video_filter = f"setpts={pts:.6f}*PTS"

    if keep_audio:
        audio_filter = _atempo_chain(speed)
        return [
            "-nostdin",
            "-protocol_whitelist", "file,pipe",
            "-i", input_path,
            "-filter_complex",
            f"[0:v]{video_filter}[v];[0:a]{audio_filter}[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-y", output_path,
        ]
    else:
        return [
            "-nostdin",
            "-protocol_whitelist", "file,pipe",
            "-i", input_path,
            "-filter:v", video_filter,
            "-an",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
            "-movflags", "+faststart",
            "-y", output_path,
        ]


# ────────────────────────────────────────────────────────────────────────────
# 13. mix_audio — Overlay background music track
# ────────────────────────────────────────────────────────────────────────────

def _mix_audio(input_path: str, output_path: str, options: dict, extra_inputs: dict, **_) -> list[str]:
    bg_path = extra_inputs.get("bg_music_url", "")
    if not bg_path:
        raise ValueError("mix_audio requires bg_music_url option with a valid URL")

    main_vol = float(options.get("main_volume", 1.0))
    bg_vol   = float(options.get("bg_volume", 0.15))
    crf = int(options.get("crf", 23))

    # Loop background music for the entire video duration, then mix
    audio_filter = (
        f"[1:a]aloop=loop=-1:size=2e+09,volume={bg_vol}[bg];"
        f"[0:a]volume={main_vol}[main];"
        f"[main][bg]amix=inputs=2:duration=first:dropout_transition=2[out]"
    )

    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-i", bg_path,
        "-filter_complex", audio_filter,
        "-map", "0:v",
        "-map", "[out]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 14. fade — Fade in/out effects (video + audio)
# ────────────────────────────────────────────────────────────────────────────

def _fade(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    fade_in  = float(options.get("fade_in_duration", 0.5))
    fade_out = float(options.get("fade_out_duration", 0.5))
    fade_out_start = options.get("fade_out_start")
    audio_fade = str(options.get("audio_fade", "true")).lower() != "false"
    crf = int(options.get("crf", 23))

    vf_parts = []
    af_parts = []

    if fade_in > 0:
        vf_parts.append(f"fade=t=in:st=0:d={fade_in}")
        if audio_fade:
            af_parts.append(f"afade=t=in:st=0:d={fade_in}")

    if fade_out > 0 and fade_out_start is not None:
        vf_parts.append(f"fade=t=out:st={fade_out_start}:d={fade_out}")
        if audio_fade:
            af_parts.append(f"afade=t=out:st={fade_out_start}:d={fade_out}")

    args = [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
    ]
    if vf_parts:
        args += ["-vf", ",".join(vf_parts)]
    if af_parts:
        args += ["-af", ",".join(af_parts)]
    args += [
        "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-y", output_path,
    ]
    return args


# ────────────────────────────────────────────────────────────────────────────
# 15. add_text — Burn text overlay (captions, CTAs, brand name)
# ────────────────────────────────────────────────────────────────────────────

def _add_text(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    text       = options.get("text", "Sample Text")
    x          = options.get("x", "(w-text_w)/2")
    y          = options.get("y", "h-th-40")
    fontsize   = int(options.get("fontsize", 52))
    fontcolor  = options.get("fontcolor", "white")
    box        = str(options.get("box", "true")).lower() != "false"
    boxcolor   = options.get("boxcolor", "black@0.55")
    boxborder  = int(options.get("boxborderw", 8))
    start_time = options.get("start_time")
    end_time   = options.get("end_time")
    crf = int(options.get("crf", 23))

    # Escape special drawtext characters
    safe_text = text.replace("'", "\\'").replace(":", "\\:").replace("\\", "\\\\")

    draw = (
        f"drawtext=text='{safe_text}'"
        f":x={x}:y={y}"
        f":fontsize={fontsize}"
        f":fontcolor={fontcolor}"
    )
    if box:
        draw += f":box=1:boxcolor={boxcolor}:boxborderw={boxborder}"

    if start_time is not None:
        draw += f":enable='between(t\\,{start_time}\\,{end_time or 99999})'"

    return [
        "-nostdin",
        "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-vf", draw,
        "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 16. gif_export — Optimized GIF with palette generation
# ────────────────────────────────────────────────────────────────────────────

def _gif_export(input_path: str, output_path: str, options: dict, **_) -> list[str]:
    fps      = int(options.get("fps", 15))
    width    = int(options.get("width", 480))
    start    = options.get("start_time", 0)
    duration = options.get("duration", 5)

    # Single-pass palette-optimized GIF generation
    vf = (
        f"fps={fps},scale={width}:-1:flags=lanczos,"
        f"split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer"
    )
    args = ["-nostdin", "-protocol_whitelist", "file,pipe"]
    if start:
        args += ["-ss", str(start)]
    args += ["-i", input_path]
    if duration:
        args += ["-t", str(duration)]
    args += ["-vf", vf, "-loop", "0", "-y", output_path]
    return args


# ════════════════════════════════════════════════════════════════════════════
# Registry
# ════════════════════════════════════════════════════════════════════════════



# ════════════════════════════════════════════════════════════════════════════
# New Presets: v2 additions
# ════════════════════════════════════════════════════════════════════════════


# ────────────────────────────────────────────────────────────────────────────
# 17. replace_audio
# ────────────────────────────────────────────────────────────────────────────

def _replace_audio(input_path, output_path, options, extra_inputs, **_):
    """Replace video audio track with a new audio file (TTS/voiceover)."""
    audio_path = extra_inputs.get("input_audio_url", "")
    if not audio_path:
        raise ValueError("replace_audio requires input_audio_url")
    volume = float(options.get("volume", 1.0))
    loop_audio = str(options.get("loop_audio", "false")).lower() == "true"
    shortest = str(options.get("shortest", "true")).lower() != "false"
    audio_bitrate = options.get("audio_bitrate", "192k")
    args = ["-nostdin", "-protocol_whitelist", "file,pipe",
            "-i", input_path, "-i", audio_path]
    if loop_audio or volume != 1.0:
        chain = []
        if loop_audio:
            chain.append("aloop=loop=-1:size=2e+09")
        if volume != 1.0:
            chain.append(f"volume={volume}")
        af = ",".join(chain)
        args += ["-filter_complex", f"[1:a]{af}[aout]",
                 "-map", "0:v", "-map", "[aout]"]
    else:
        args += ["-map", "0:v", "-map", "1:a"]
    args += ["-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate]
    if shortest:
        args.append("-shortest")
    args += ["-movflags", "+faststart", "-y", output_path]
    return args


# ────────────────────────────────────────────────────────────────────────────
# 18. resize_for_platform
# ────────────────────────────────────────────────────────────────────────────

_PLATFORM_PROFILES = {
    "tiktok":           {"width": 1080, "height": 1920, "fps": 30,  "crf": 23, "audio_bitrate": "128k", "max_bitrate": "4000k"},
    "instagram_reels":  {"width": 1080, "height": 1920, "fps": 30,  "crf": 23, "audio_bitrate": "128k", "max_bitrate": "3500k"},
    "instagram_feed":   {"width": 1080, "height": 1080, "fps": 30,  "crf": 23, "audio_bitrate": "128k", "max_bitrate": "3500k"},
    "youtube_shorts":   {"width": 1080, "height": 1920, "fps": 60,  "crf": 20, "audio_bitrate": "192k", "max_bitrate": "8000k"},
    "youtube":          {"width": 1920, "height": 1080, "fps": 60,  "crf": 18, "audio_bitrate": "192k", "max_bitrate": "8000k"},
    "facebook_ads":     {"width": 1080, "height": 1080, "fps": 30,  "crf": 23, "audio_bitrate": "128k", "max_bitrate": "4000k"},
    "whatsapp":         {"width": 854,  "height": 480,  "fps": 25,  "crf": 28, "audio_bitrate": "96k",  "max_bitrate": "1200k"},
    "twitter":          {"width": 1280, "height": 720,  "fps": 30,  "crf": 23, "audio_bitrate": "128k", "max_bitrate": "5000k"},
    "linkedin":         {"width": 1920, "height": 1080, "fps": 30,  "crf": 23, "audio_bitrate": "128k", "max_bitrate": "5000k"},
}


def _resize_for_platform(input_path, output_path, options, **_):
    """Re-encode for a specific social platform (tiktok, instagram_reels, youtube, etc.)."""
    platform = options.get("platform", "tiktok").lower()
    if platform not in _PLATFORM_PROFILES:
        raise ValueError(f"Unknown platform '{platform}'. Available: {list(_PLATFORM_PROFILES.keys())}")
    prof = _PLATFORM_PROFILES[platform]
    w   = int(options.get("width",  prof["width"]))
    h   = int(options.get("height", prof["height"]))
    fps = int(options.get("fps",    prof["fps"]))
    crf = int(options.get("crf",    prof["crf"]))
    abr = options.get("audio_bitrate", prof["audio_bitrate"])
    max_br = options.get("max_bitrate", prof["max_bitrate"])
    fit = options.get("fit", "pad").lower()
    if fit == "crop":
        vf = (f"scale=if(gt(iw/ih\\,{w}/{h})\\,{h}*iw/ih\\,{w}):"
              f"if(gt(iw/ih\\,{w}/{h})\\,{h}\\,{w}*ih/iw),"
              f"crop={w}:{h},fps={fps}")
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps}")
    bufsize = f"{int(max_br[:-1])*2}k"
    return [
        "-nostdin", "-protocol_whitelist", "file,pipe",
        "-i", input_path, "-vf", vf,
        "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
        "-maxrate", max_br, "-bufsize", bufsize,
        "-c:a", "aac", "-b:a", abr,
        "-movflags", "+faststart", "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 19. compress_video
# ────────────────────────────────────────────────────────────────────────────

def _compress_video(input_path, output_path, options, work_dir, **_):
    """Compress video. Use target_size_mb for 2-pass, or crf for simple mode."""
    import subprocess
    max_width  = int(options.get("max_width", 1280))
    max_height = int(options.get("max_height", 720))
    audio_bitrate = options.get("audio_bitrate", "96k")
    target_mb = options.get("target_size_mb")
    vf = (f"scale='min({max_width},iw)':'min({max_height},ih)'"
          ":force_original_aspect_ratio=decrease,"
          "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if target_mb:
        target_mb = float(target_mb)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, timeout=30)
        try:
            duration = float(probe.stdout.strip())
        except Exception:
            duration = 60.0
        audio_kbps = int(audio_bitrate.replace("k", "").replace("K", ""))
        total_kbps = (target_mb * 8 * 1024) / duration
        video_kbps = max(int(total_kbps - audio_kbps), 100)
        video_br = f"{video_kbps}k"
        passlog = os.path.join(work_dir, "ffmpeg2pass")
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", input_path,
             "-vf", vf, "-c:v", "libx264", "-b:v", video_br, "-preset", "slow",
             "-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", "-"],
            capture_output=True, timeout=600)
        return [
            "-nostdin", "-i", input_path, "-vf", vf,
            "-c:v", "libx264", "-b:v", video_br, "-preset", "slow",
            "-pass", "2", "-passlogfile", passlog,
            "-c:a", "aac", "-b:a", audio_bitrate,
            "-movflags", "+faststart", "-y", output_path,
        ]
    crf = int(options.get("crf", 28))
    return [
        "-nostdin", "-protocol_whitelist", "file,pipe",
        "-i", input_path, "-vf", vf,
        "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart", "-y", output_path,
    ]


# ────────────────────────────────────────────────────────────────────────────
# 20. clear_silence — Two-pass: detect silence → cut → stitch
# ────────────────────────────────────────────────────────────────────────────

def _clear_silence(input_path, output_path, options, work_dir, **_):
    """Remove silent segments from video, returning a seamless continuous video.

    Pass 1: silencedetect finds silence timestamps in stderr.
    Pass 2: trim+atrim per segment, then concat filter stitches them.

    Options:
        noise_level (str, default "-35dB"):  silence threshold.
        min_silence_duration (float, default 0.5): min silence length to cut (s).
        padding (float, default 0.1): seconds of audio to keep around cuts.
        crf (int, default 23): output quality.
    """
    import subprocess, re
    noise_level = options.get("noise_level", "-35dB")
    min_silence = float(options.get("min_silence_duration", 0.5))
    padding     = float(options.get("padding", 0.1))
    crf         = int(options.get("crf", 23))

    # ── Pass 1: detect silence ──────────────────────────────────────────────
    detect = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", input_path,
         "-af", f"silencedetect=n={noise_level}:d={min_silence}",
         "-vn", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    stderr = detect.stderr

    starts = list(map(float, re.findall(r"silence_start: ([\d.e+\-]+)", stderr)))
    ends   = list(map(float, re.findall(r"silence_end: ([\d.e+\-]+)",   stderr)))

    dur_m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", stderr)
    total = (int(dur_m.group(1))*3600 + int(dur_m.group(2))*60 + float(dur_m.group(3))) if dur_m else 9999.0

    # File ends during silence — add synthetic end
    if len(ends) < len(starts):
        ends.append(total)

    # ── Build non-silent keep intervals ────────────────────────────────────
    keep = []
    prev = 0.0
    for s_start, s_end in zip(starts, ends):
        seg_end   = s_start - padding
        seg_start = prev
        if seg_end - seg_start > 0.15:
            keep.append((max(0.0, seg_start), seg_end))
        prev = s_end + padding
    if total - prev > 0.15:
        keep.append((prev, total))

    # No silence found — just stream-copy
    if not keep or not starts:
        return ["-nostdin", "-protocol_whitelist", "file,pipe",
                "-i", input_path, "-c", "copy", "-y", output_path]

    # ── Pass 2: trim each segment + concat all ─────────────────────────────
    n = len(keep)
    fc_parts = []
    for i, (s, e) in enumerate(keep):
        fc_parts.append(
            f"[0:v]trim=start={s:.4f}:end={e:.4f},setpts=PTS-STARTPTS[v{i}]")
        fc_parts.append(
            f"[0:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS[a{i}]")
    interleaved = "".join(f"[v{i}][a{i}]" for i in range(n))
    fc_parts.append(f"{interleaved}concat=n={n}:v=1:a=1[outv][outa]")

    return [
        "-nostdin", "-protocol_whitelist", "file,pipe",
        "-i", input_path,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-y", output_path,
    ]


PRESETS: dict[str, PresetDef] = {
    # ── Basic ──────────────────────────────────────────────────────────────
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
    "extract_audio_mp3": PresetDef(
        name="extract_audio_mp3",
        description="Extract audio track as MP3",
        extra_input_fields=[],
        build_cmd=_extract_audio_mp3,
        defaults={"audio_bitrate": "128k"},
    ),

    # ── Overlays ───────────────────────────────────────────────────────────
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
    "add_text": PresetDef(
        name="add_text",
        description="Burn text overlay — captions, CTAs, brand name",
        extra_input_fields=[],
        build_cmd=_add_text,
        defaults={
            "text": "Sample Text",
            "x": "(w-text_w)/2",
            "y": "h-th-40",
            "fontsize": 52,
            "fontcolor": "white",
            "box": "true",
            "boxcolor": "black@0.55",
            "boxborderw": 8,
            "crf": 23,
        },
    ),

    # ── Social Media / UGC ─────────────────────────────────────────────────
    "crop_to_aspect": PresetDef(
        name="crop_to_aspect",
        description="Center-crop video to target aspect ratio (9:16, 4:5, 1:1, 16:9)",
        extra_input_fields=[],
        build_cmd=_crop_to_aspect,
        defaults={"aspect_ratio": "9:16", "crf": 23},
    ),
    "trim": PresetDef(
        name="trim",
        description="Trim video to a time range (stream-copy, instant)",
        extra_input_fields=[],
        build_cmd=_trim,
        defaults={"start_time": "0"},
    ),
    "speed_change": PresetDef(
        name="speed_change",
        description="Change playback speed (0.25x–4x, slowmo or fast-forward)",
        extra_input_fields=[],
        build_cmd=_speed_change,
        defaults={"speed": 1.5, "crf": 23, "keep_audio": "true"},
    ),
    "fade": PresetDef(
        name="fade",
        description="Add fade-in and/or fade-out (video + audio)",
        extra_input_fields=[],
        build_cmd=_fade,
        defaults={"fade_in_duration": 0.5, "fade_out_duration": 0.5, "audio_fade": "true", "crf": 23},
    ),
    "gif_export": PresetDef(
        name="gif_export",
        description="Export video clip as optimized GIF (palette-based)",
        extra_input_fields=[],
        build_cmd=_gif_export,
        defaults={"fps": 15, "width": 480, "start_time": 0, "duration": 5},
    ),

    # ── Audio ──────────────────────────────────────────────────────────────
    "normalize_loudness": PresetDef(
        name="normalize_loudness",
        description="Normalize audio loudness (EBU R128) — YouTube/TikTok/broadcast",
        extra_input_fields=[],
        build_cmd=_normalize_loudness,
        defaults={"target_lufs": -14.0, "target_lra": 7.0, "target_tp": -1.0},
    ),
    "mix_audio": PresetDef(
        name="mix_audio",
        description="Mix background music into video with volume control",
        extra_input_fields=["bg_music_url"],
        build_cmd=_mix_audio,
        defaults={"main_volume": 1.0, "bg_volume": 0.15, "crf": 23},
    ),

    # ── Delivery ───────────────────────────────────────────────────────────
    "concat_videos": PresetDef(
        name="concat_videos",
        description="Concatenate multiple videos (stream copy)",
        extra_input_fields=["input_urls"],
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

    "replace_audio": PresetDef(
        name="replace_audio",
        description="Replace video audio with TTS/voiceover file (ElevenLabs etc.)",
        extra_input_fields=["input_audio_url"],
        build_cmd=_replace_audio,
        defaults={"volume": 1.0, "loop_audio": "false", "shortest": "true", "audio_bitrate": "192k"},
    ),
    "resize_for_platform": PresetDef(
        name="resize_for_platform",
        description="Re-encode for platform: tiktok/instagram_reels/instagram_feed/youtube_shorts/youtube/facebook_ads/whatsapp/twitter/linkedin",
        extra_input_fields=[],
        build_cmd=_resize_for_platform,
        defaults={"platform": "tiktok", "fit": "pad"},
    ),
    "compress_video": PresetDef(
        name="compress_video",
        description="Compress video to small file size. Use target_size_mb for 2-pass or crf for simple.",
        extra_input_fields=[],
        build_cmd=_compress_video,
        defaults={"max_width": 1280, "max_height": 720, "audio_bitrate": "96k", "crf": 28},
    ),
    "clear_silence": PresetDef(
        name="clear_silence",
        description="Detect and remove silent segments, return seamless video (2-pass: silencedetect + trim+concat)",
        extra_input_fields=[],
        build_cmd=_clear_silence,
        defaults={"noise_level": "-35dB", "min_silence_duration": 0.5, "padding": 0.1, "crf": 23},
    ),

}


def get_preset(name: str) -> PresetDef:
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: '{name}'. Available: {list(PRESETS.keys())}")
    return PRESETS[name]
