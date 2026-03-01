# FFmpeg Executor API

Internal HTTP service for async video processing. Two modes:

- **Preset API** (`/jobs`) — ready-made presets for common tasks (thumbnail, transcode, etc.)
- **Command API** (`/v1/commands`) — raw FFmpeg commands, Rendi-compatible

**Base URL:** `https://ffmpeg-api.kuprino.com`

---

## Architecture

```
caller (n8n / Claude / curl)
        │
        ├─ POST /v1/commands  ──► raw FFmpeg command
        └─ POST /jobs         ──► preset
                │
           FastAPI (api)
                │  enqueue Celery task
                ▼
          Celery Worker
                │
         ┌──────┴──────┐
     download        FFmpeg
     (SSRF check)    subprocess
                         │
                         ▼
                  Cloudflare R2
                  (public URL)
                         │
                    webhook (opt)
```

- **API** — FastAPI, responds immediately with `command_id` / `job_id`
- **Worker** — Celery + Redis, executes the pipeline asynchronously
- **Storage** — Cloudflare R2, results accessible via `pub-*.r2.dev` public URL
- **DB** — PostgreSQL, persists all job/command state

---

## Command API — raw FFmpeg (Rendi-compatible)

Full FFmpeg expressivity: pass any FFmpeg arguments as a string.
Input and output files are referenced via `{{alias}}` placeholders.

### POST /v1/commands

**Request:**

| Field | Type | Required | Description |
|---|---|---|---|
| `ffmpeg_command` | string | ✅ | FFmpeg arguments (no `ffmpeg` word). Use `{{in_alias}}` and `{{out_alias}}` for files |
| `input_files` | object | ✅ | `{ "in_alias": "https://..." }` — aliases **must** start with `in_` |
| `output_files` | object | ✅ | `{ "out_alias": "filename.ext" }` — aliases **must** start with `out_` |
| `webhook_url` | string | — | HTTPS URL to POST result to on completion |

**Response `202`:**
```json
{ "command_id": "9bc09dc6-cf96-433f-885b-7e1e63350fac" }
```

**Example — extract thumbnail:**
```bash
curl -X POST https://ffmpeg-api.kuprino.com/v1/commands \
  -H "Content-Type: application/json" \
  -d '{
    "ffmpeg_command": "-i {{in_video}} -ss 5 -frames:v 1 -q:v 2 {{out_thumb}}",
    "input_files": {
      "in_video": "https://example.com/video.mp4"
    },
    "output_files": {
      "out_thumb": "thumb.jpg"
    }
  }'
```

**Example — transcode to 720p with custom settings:**
```bash
curl -X POST https://ffmpeg-api.kuprino.com/v1/commands \
  -H "Content-Type: application/json" \
  -d '{
    "ffmpeg_command": "-i {{in_src}} -vf scale=1280:720 -c:v libx264 -crf 20 -preset fast -c:a aac -b:a 192k -movflags +faststart {{out_720p}}",
    "input_files": { "in_src": "https://example.com/4k.mp4" },
    "output_files": { "out_720p": "720p.mp4" }
  }'
```

**Example — extract audio:**
```bash
curl -X POST https://ffmpeg-api.kuprino.com/v1/commands \
  -H "Content-Type: application/json" \
  -d '{
    "ffmpeg_command": "-i {{in_video}} -vn -c:a libmp3lame -b:a 128k {{out_audio}}",
    "input_files": { "in_video": "https://example.com/video.mp4" },
    "output_files": { "out_audio": "audio.mp3" }
  }'
```

**Example — multiple outputs (thumbnail + 720p in one run):**
```bash
curl -X POST https://ffmpeg-api.kuprino.com/v1/commands \
  -H "Content-Type: application/json" \
  -d '{
    "ffmpeg_command": "-i {{in_src}} -ss 3 -frames:v 1 {{out_thumb}} -vf scale=1280:720 -c:v libx264 -crf 23 {{out_video}}",
    "input_files": { "in_src": "https://example.com/video.mp4" },
    "output_files": {
      "out_thumb": "preview.jpg",
      "out_video": "720p.mp4"
    }
  }'
```

**Example — overlay watermark:**
```bash
curl -X POST https://ffmpeg-api.kuprino.com/v1/commands \
  -H "Content-Type: application/json" \
  -d '{
    "ffmpeg_command": "-i {{in_video}} -i {{in_logo}} -filter_complex overlay=W-w-10:H-h-10 -c:v libx264 -crf 23 {{out_result}}",
    "input_files": {
      "in_video": "https://example.com/video.mp4",
      "in_logo": "https://example.com/logo.png"
    },
    "output_files": { "out_result": "watermarked.mp4" }
  }'
```

### GET /v1/commands/{command_id}

**Response:**

| Field | Type | Description |
|---|---|---|
| `command_id` | UUID | |
| `status` | string | `QUEUED` / `DOWNLOADING` / `PROCESSING` / `UPLOADING` / `SUCCESS` / `FAILED` |
| `ffmpeg_command` | string | Original command |
| `input_files` | object | Input aliases → URLs |
| `output_files` | object | Output aliases → `{ url, size_bytes }` |
| `created_at` | datetime | |
| `started_at` | datetime \| null | |
| `finished_at` | datetime \| null | |
| `duration_seconds` | float \| null | |
| `error` | string \| null | Set on FAILED |

**Example response (SUCCESS):**
```json
{
  "command_id": "9bc09dc6-cf96-433f-885b-7e1e63350fac",
  "status": "SUCCESS",
  "stage": "DONE",
  "ffmpeg_command": "-i {{in_video}} -ss 2 -frames:v 1 -q:v 3 {{out_thumb}}",
  "input_files": { "in_video": "https://example.com/video.mp4" },
  "output_files": {
    "out_thumb": {
      "url": "https://pub-9211a70aab784d6bb4f9bfdbee2871c1.r2.dev/ffmpeg-results/9bc09dc6-.../thumb.jpg",
      "size_bytes": 42547
    }
  },
  "duration_seconds": 0.64,
  "error": null
}
```

```bash
curl https://ffmpeg-api.kuprino.com/v1/commands/9bc09dc6-cf96-433f-885b-7e1e63350fac
```

### Security rules for ffmpeg_command

The command string is validated before execution. **Blocked patterns:**

| Pattern | Reason |
|---|---|
| `https://`, `http://`, `tcp://`, `rtsp://` etc. | All inputs must go through `input_files` |
| `/etc/`, `/proc/`, `/sys/`, `/home/`, `..` | Filesystem escape prevention |
| `script=` in filters | Arbitrary code execution |
| `` ` ``, `$(` | Shell injection |
| `pipe:\|...` | Command piping |

Flags `-nostdin -protocol_whitelist file,pipe` are prepended automatically to every command.

---

## Preset API

Ready-made presets for common tasks. Simpler to use than raw commands.

### POST /jobs

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `preset` | string | ✅ | Preset name |
| `input_url` | string | ✅ | HTTPS URL of the source file |
| `output_filename` | string | ✅ | Output filename |
| `preset_options` | object | — | Preset-specific parameters |
| `webhook_url` | string | — | HTTPS callback URL |
| `metadata` | object | — | Arbitrary key-value data stored with the job |

**Response `202`:**
```json
{ "job_id": "98906deb-9a70-46a6-bf2e-c979d470cb37" }
```

### GET /jobs/{job_id}

Same structure as `/v1/commands/{id}`, but with single `output_url` (string) instead of `output_files` dict.

---

## Available Presets

### BASIC PRESETS

#### `transcode_h264_mp4`
Convert any video to H.264 + AAC, MP4 container.

| Option | Default | Description |
|---|---|---|
| `crf` | `23` | Quality (0=lossless → 51=worst) |
| `audio_bitrate` | `"128k"` | Audio bitrate |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"transcode_h264_mp4","input_url":"https://example.com/video.avi","output_filename":"out.mp4","preset_options":{"crf":20}}'
```

#### `scale_fit_max`
Scale down to fit within max dimensions (never upscales), preserve aspect ratio.

| Option | Default | Description |
|---|---|---|
| `max_width` | `1920` | Max width px |
| `max_height` | `1080` | Max height px |
| `crf` | `23` | Quality |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"scale_fit_max","input_url":"https://example.com/4k.mp4","output_filename":"scaled.mp4","preset_options":{"max_width":1280,"max_height":720}}'
```

#### `thumbnail_jpg`
Extract a single frame as JPEG.

| Option | Default | Description |
|---|---|---|
| `at_seconds` | `1.0` | Timestamp to grab frame |
| `quality` | `5` | JPEG quality (1=best, 31=worst) |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"thumbnail_jpg","input_url":"https://example.com/video.mp4","output_filename":"thumb.jpg","preset_options":{"at_seconds":5.0}}'
```

#### `extract_audio_mp3`
Extract audio track as MP3.

| Option | Default | Description |
|---|---|---|
| `audio_bitrate` | `"128k"` | Bitrate |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"extract_audio_mp3","input_url":"https://example.com/video.mp4","output_filename":"audio.mp3","preset_options":{"audio_bitrate":"192k"}}'
```

---

### OVERLAYS & TEXT

#### `burn_subs`
Burn SRT/ASS subtitles permanently into video.

| Option | Default | Description |
|---|---|---|
| `input_subs_url` | — | ⚠️ Required. URL of `.srt` or `.ass` file |
| `crf` | `23` | Quality |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"burn_subs","input_url":"https://example.com/video.mp4","output_filename":"subbed.mp4","preset_options":{"input_subs_url":"https://example.com/subs.srt"}}'
```

#### `overlay_image`
Overlay PNG/JPG watermark at a given position.

| Option | Default | Description |
|---|---|---|
| `input_overlay_url` | — | ⚠️ Required. URL of overlay image |
| `x` | `10` | X offset px |
| `y` | `10` | Y offset px |
| `crf` | `23` | Quality |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"overlay_image","input_url":"https://example.com/video.mp4","output_filename":"watermarked.mp4","preset_options":{"input_overlay_url":"https://example.com/logo.png","x":20,"y":20}}'
```

#### `add_text`
Burn text overlay (captions, CTAs, brand name).

| Option | Default | Description |
|---|---|---|
| `text` | — | ⚠️ Required. Text to display |
| `x` | `"(w-text_w)/2"` | X position (centered by default) |
| `y` | `"h-th-40"` | Y position (bottom by default) |
| `fontsize` | `52` | Font size px |
| `fontcolor` | `"white"` | Font color |
| `box` | `"true"` | Draw background box |
| `boxcolor` | `"black@0.55"` | Box background color + alpha |
| `boxborderw` | `8` | Box border width |
| `start_time` | — | Start time (seconds) |
| `end_time` | — | End time (seconds) |
| `crf` | `23` | Quality |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"add_text","input_url":"https://example.com/video.mp4","output_filename":"captioned.mp4","preset_options":{"text":"Shop Now!","fontsize":60,"fontcolor":"white","start_time":0,"end_time":5}}'
```

---

### SOCIAL MEDIA & UGC

#### `crop_to_aspect`
Center-crop to target aspect ratio (reformat landscape for vertical platforms).

| Option | Default | Description |
|---|---|---|
| `aspect_ratio` | `"9:16"` | Target ratio (`"9:16"`, `"4:5"`, `"1:1"`, `"16:9"`) |
| `crf` | `23` | Quality |

**Use cases:** TikTok/Instagram Reels (9:16), Instagram feed (4:5), YouTube (16:9), square (1:1)

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"crop_to_aspect","input_url":"https://example.com/landscape.mp4","output_filename":"vertical.mp4","preset_options":{"aspect_ratio":"9:16"}}'
```

#### `trim`
Trim video to time range (stream-copy, instant processing).

| Option | Default | Description |
|---|---|---|
| `start_time` | `"0"` | Start time (seconds or HH:MM:SS) |
| `end_time` | — | End time (seconds or HH:MM:SS) |
| `duration` | — | Duration (seconds or HH:MM:SS) — use either `end_time` or `duration` |

**Use cases:** Cut clips from longer videos, extract highlights, trim dead air

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"trim","input_url":"https://example.com/long-video.mp4","output_filename":"clip.mp4","preset_options":{"start_time":"10","duration":"30"}}'
```

#### `speed_change`
Change playback speed (0.25x to 4x range).

| Option | Default | Description |
|---|---|---|
| `speed` | `1.5` | Speed multiplier (0.25–4.0) |
| `keep_audio` | `"true"` | Preserve audio pitch (true) or speed audio too (false) |
| `crf` | `23` | Quality |

**Use cases:** Slow-motion (0.5x), time-lapse (2x–4x), fast-forward, UGC transitions

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"speed_change","input_url":"https://example.com/video.mp4","output_filename":"slowmo.mp4","preset_options":{"speed":0.5,"keep_audio":"true"}}'
```

#### `fade`
Fade in/out effects for both video and audio.

| Option | Default | Description |
|---|---|---|
| `fade_in_duration` | `0.5` | Fade-in length (seconds) |
| `fade_out_duration` | `0.5` | Fade-out length (seconds) |
| `fade_out_start` | — | When fade-out begins (seconds from end, or absolute time) |
| `audio_fade` | `"true"` | Apply fade to audio too |
| `crf` | `23` | Quality |

**Use cases:** Professional transitions, ad clips, smooth intros/outros

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"fade","input_url":"https://example.com/video.mp4","output_filename":"faded.mp4","preset_options":{"fade_in_duration":1.0,"fade_out_duration":1.0,"audio_fade":"true"}}'
```

#### `gif_export`
Export video as optimized GIF with custom frame rate and resolution.

| Option | Default | Description |
|---|---|---|
| `fps` | `15` | Frames per second |
| `width` | `480` | Output width px |
| `start_time` | `0` | Start timestamp (seconds) |
| `duration` | `5` | Duration to export (seconds) |

**Use cases:** Social preview GIFs, ad previews, animated thumbnails

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"gif_export","input_url":"https://example.com/video.mp4","output_filename":"preview.gif","preset_options":{"fps":15,"width":480,"duration":5}}'
```

---

### AUDIO

#### `normalize_loudness`
EBU R128 loudness normalization (LUFS-based).

| Option | Default | Description |
|---|---|---|
| `target_lufs` | `-14.0` | Target loudness in LUFS |
| `target_lra` | `7.0` | Loudness range (LRA) |
| `target_tp` | `-1.0` | True peak limit |

**Use cases:**
- YouTube/Spotify: -14 LUFS
- TikTok/Instagram/Meta: -16 LUFS
- Broadcast: -23 LUFS

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"normalize_loudness","input_url":"https://example.com/video.mp4","output_filename":"normalized.mp4","preset_options":{"target_lufs":-14.0}}'
```

#### `mix_audio`
Mix background music into video (loops if shorter).

| Option | Default | Description |
|---|---|---|
| `bg_music_url` | — | ⚠️ Required. URL of background music file |
| `bg_volume` | `0.15` | Background music volume (0–1) |
| `main_volume` | `1.0` | Original audio volume (0–1) |
| `crf` | `23` | Quality |

**Use cases:** Add music loops to UGC clips, background tracks for ads, licensed music beds

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"mix_audio","input_url":"https://example.com/video.mp4","output_filename":"with_music.mp4","preset_options":{"bg_music_url":"https://example.com/music.mp3","bg_volume":0.2}}'
```

---

### DELIVERY & PACKAGING

#### `concat_videos`
Concatenate multiple videos via stream copy (same codec/resolution required).

| Option | Default | Description |
|---|---|---|
| `input_urls` | — | ⚠️ Required. Array of additional video URLs (in order after `input_url`) |

**Use cases:** Compile highlight reels, merge video segments, create compilations

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"concat_videos","input_url":"https://example.com/video1.mp4","output_filename":"merged.mp4","preset_options":{"input_urls":["https://example.com/video2.mp4","https://example.com/video3.mp4"]}}'
```

#### `hls_package`
Package video as HLS playlist + segments, output as `.zip`.

| Option | Default | Description |
|---|---|---|
| `hls_time` | `6` | Segment duration (seconds) |
| `video_bitrate` | `"1000k"` | Target video bitrate |

**Use cases:** Adaptive bitrate streaming, CDN delivery, multi-quality playback

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"preset":"hls_package","input_url":"https://example.com/video.mp4","output_filename":"stream.zip","preset_options":{"hls_time":6,"video_bitrate":"2500k"}}'
```

---

## Job / Command lifecycle

```
QUEUED → DOWNLOADING → PROCESSING → UPLOADING → SUCCESS
                                              ↘ FAILED
```

Poll until `status` is `SUCCESS` or `FAILED`. Typical time: 0.5–30s.

---

## Webhook

On completion (SUCCESS or FAILED), the service POSTs to `webhook_url`:

**Command webhook:**
```json
{
  "command_id": "...",
  "status": "SUCCESS",
  "output_files": { "out_thumb": { "url": "https://...", "size_bytes": 42547 } },
  "duration_seconds": 0.64
}
```

**Job webhook:**
```json
{
  "job_id": "...",
  "status": "SUCCESS",
  "output_url": "https://...",
  "duration_seconds": 1.2
}
```

---

## MCP Server

The `mcp/` directory contains an MCP server that exposes the API as tools for Claude and other LLM clients.

### Tools

| Tool | Description |
|---|---|
| `ffmpeg_run_command` | Submit a raw FFmpeg command (polls until done by default) |
| `ffmpeg_run_preset` | Submit a preset job (supports all 16 presets) |
| `ffmpeg_get_command` | Get command status by ID |
| `ffmpeg_get_job` | Get job status by ID |
| `ffmpeg_health` | Check service health |

### Available Presets (16 total)

**BASIC:** `transcode_h264_mp4`, `scale_fit_max`, `thumbnail_jpg`, `extract_audio_mp3`

**OVERLAYS:** `burn_subs`, `overlay_image`, `add_text`

**SOCIAL MEDIA / UGC:** `crop_to_aspect`, `trim`, `speed_change`, `fade`, `gif_export`

**AUDIO:** `normalize_loudness`, `mix_audio`

**DELIVERY:** `concat_videos`, `hls_package`

### Setup

**Requirements:** Python 3.10+, `pip install httpx`

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "ffmpeg-executor": {
      "command": "python3",
      "args": ["/path/to/ffmpeg-executor/mcp/server.py"],
      "env": {
        "FFMPEG_API_BASE": "https://ffmpeg-api.kuprino.com"
      }
    }
  }
}
```

**Cursor** (`.cursor/mcp.json` in project root):
```json
{
  "mcpServers": {
    "ffmpeg-executor": {
      "command": "python3",
      "args": ["mcp/server.py"],
      "env": { "FFMPEG_API_BASE": "https://ffmpeg-api.kuprino.com" }
    }
  }
}
```

**n8n MCP Client node:**
```
Command: python3
Args: ["/path/to/mcp/server.py"]
Env: FFMPEG_API_BASE=https://ffmpeg-api.kuprino.com
```

### Example Claude prompt

Once connected via MCP:
```
Сделай превью для этого видео: https://example.com/video.mp4
→ ffmpeg_run_preset({ preset: "thumbnail_jpg", input_url: "https://example.com/video.mp4", output_filename: "thumb.jpg" })
→ returns public URL to the JPEG
```

### Example: Create UGC Ad from Raw Video

```
Реформат видео для TikTok (9:16), добавь текст "Shop Now!", нормализуй звук и сделай превью
→ ffmpeg_run_preset({ preset: "crop_to_aspect", input_url: "...", output_filename: "vertical.mp4", preset_options: { aspect_ratio: "9:16" } })
→ ffmpeg_run_preset({ preset: "add_text", input_url: "[result]", output_filename: "with_cta.mp4", preset_options: { text: "Shop Now!" } })
→ ffmpeg_run_preset({ preset: "normalize_loudness", input_url: "[result]", output_filename: "normalized.mp4", preset_options: { target_lufs: -16.0 } })
→ ffmpeg_run_preset({ preset: "thumbnail_jpg", input_url: "[result]", output_filename: "preview.jpg" })
```

---

## Error handling

| HTTP | Meaning |
|---|---|
| `202` | Accepted |
| `400` | Bad request — unsafe URL, invalid preset/alias, blocked FFmpeg pattern |
| `404` | Not found |
| `422` | Missing required fields |

---

## Using from n8n

**Poll pattern (Command API):**
```
HTTP Request POST /v1/commands
  → Set: save command_id
  → Wait 5s
  → HTTP Request GET /v1/commands/{{command_id}}
  → IF status == SUCCESS → use output_files.out_result.url
  → IF status == FAILED  → handle error
  → ELSE → back to Wait
```

**Webhook pattern:**
```
HTTP Request POST /v1/commands (webhook_url = n8n Webhook node URL)
  → [n8n waits for webhook trigger]
  → Webhook node receives full result with output_files
```
