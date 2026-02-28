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

### `transcode_h264_mp4`
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

### `scale_fit_max`
Scale down to fit within max dimensions (never upscales), preserve aspect ratio.

| Option | Default | Description |
|---|---|---|
| `max_width` | `1920` | Max width px |
| `max_height` | `1080` | Max height px |
| `crf` | `23` | Quality |

### `thumbnail_jpg`
Extract a single frame as JPEG.

| Option | Default | Description |
|---|---|---|
| `at_seconds` | `1.0` | Timestamp to grab frame |
| `quality` | `5` | JPEG quality (1=best, 31=worst) |

### `burn_subs`
Burn SRT/ASS subtitles permanently into video.

| Option | Default | Description |
|---|---|---|
| `input_subs_url` | — | ⚠️ Required. URL of `.srt` or `.ass` file |
| `crf` | `23` | Quality |

### `overlay_image`
Overlay PNG/JPG watermark at a given position.

| Option | Default | Description |
|---|---|---|
| `input_overlay_url` | — | ⚠️ Required. URL of overlay image |
| `x` | `10` | X offset px |
| `y` | `10` | Y offset px |
| `crf` | `23` | Quality |

### `extract_audio_mp3`
Extract audio track as MP3.

| Option | Default | Description |
|---|---|---|
| `audio_bitrate` | `"128k"` | Bitrate |

### `concat_videos`
Concatenate videos via stream copy (same codec/resolution required).

| Option | Default | Description |
|---|---|---|
| `input_urls` | — | ⚠️ Required. Array of additional video URLs (in order after `input_url`) |

### `hls_package`
Package video as HLS playlist + segments, output as `.zip`.

| Option | Default | Description |
|---|---|---|
| `hls_time` | `6` | Segment duration (seconds) |
| `video_bitrate` | `"1000k"` | Target video bitrate |

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
| `ffmpeg_run_preset` | Submit a preset job |
| `ffmpeg_get_command` | Get command status by ID |
| `ffmpeg_get_job` | Get job status by ID |
| `ffmpeg_health` | Check service health |

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
→ ffmpeg_run_command({ ffmpeg_command: "-i {{in_v}} -ss 3 -frames:v 1 -q:v 2 {{out_thumb}}", ... })
→ returns public URL to the JPEG
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
