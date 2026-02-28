# FFmpeg Executor API

Internal HTTP service for async video processing. Accepts jobs from n8n (or any HTTP client), runs FFmpeg, uploads results to Cloudflare R2, returns a public URL.

**Base URL:** `https://ffmpeg-api.kuprino.com`

---

## Architecture

```
n8n / caller
    │
    │  POST /jobs
    ▼
FastAPI (api)
    │  enqueue
    ▼
Celery Worker ──► FFmpeg ──► Cloudflare R2
    │
    │  webhook (optional)
    ▼
caller
```

- **API** — FastAPI, accepts jobs, returns `job_id` immediately (async)
- **Worker** — Celery + Redis, downloads input, runs FFmpeg, uploads result
- **Storage** — Cloudflare R2, results accessible via public URL
- **DB** — PostgreSQL, stores job metadata and status

---

## API Reference

### POST /jobs — Submit a job

**Request body (JSON):**

| Field | Type | Required | Description |
|---|---|---|---|
| `preset` | string | ✅ | Preset name (see list below) |
| `input_url` | string | ✅ | HTTPS URL of the source video/audio |
| `output_filename` | string | ✅ | Name for the output file (e.g. `result.mp4`) |
| `preset_options` | object | — | Preset-specific parameters (see per-preset docs) |
| `webhook_url` | string | — | HTTPS URL to POST the result to on completion |
| `metadata` | object | — | Arbitrary key-value data, stored with the job |

**Response `202 Accepted`:**
```json
{
  "job_id": "98906deb-9a70-46a6-bf2e-c979d470cb37"
}
```

**Example:**
```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "thumbnail_jpg",
    "input_url": "https://example.com/video.mp4",
    "output_filename": "thumb.jpg"
  }'
```

---

### GET /jobs/{job_id} — Get job status

**Response:**

| Field | Type | Description |
|---|---|---|
| `job_id` | UUID | Job identifier |
| `status` | string | `QUEUED` / `DOWNLOADING` / `PROCESSING` / `UPLOADING` / `SUCCESS` / `FAILED` |
| `stage` | string | Current stage detail |
| `preset` | string | Preset used |
| `output_url` | string \| null | Public URL of the result (set on SUCCESS) |
| `created_at` | datetime | ISO 8601 |
| `started_at` | datetime \| null | When worker picked up the job |
| `finished_at` | datetime \| null | When job completed or failed |
| `duration_seconds` | float \| null | Total processing time |
| `error` | string \| null | Error message (set on FAILED) |

**Example:**
```bash
curl https://ffmpeg-api.kuprino.com/jobs/98906deb-9a70-46a6-bf2e-c979d470cb37
```

```json
{
  "job_id": "98906deb-9a70-46a6-bf2e-c979d470cb37",
  "status": "SUCCESS",
  "stage": "DONE",
  "preset": "thumbnail_jpg",
  "output_url": "https://pub-9211a70aab784d6bb4f9bfdbee2871c1.r2.dev/ffmpeg-results/98906deb-.../thumb.jpg",
  "created_at": "2026-02-28T19:35:03.850171Z",
  "started_at": "2026-02-28T19:35:03.863550Z",
  "finished_at": "2026-02-28T19:35:05.254764Z",
  "duration_seconds": 1.39,
  "error": null
}
```

---

### GET /health — Health check

```bash
curl https://ffmpeg-api.kuprino.com/health
```

```json
{
  "status": "ok",
  "api": true,
  "redis": true,
  "postgres": true
}
```

`status` is `"ok"` when all components are healthy, `"degraded"` otherwise.

---

## Job Lifecycle

```
QUEUED → DOWNLOADING → PROCESSING → UPLOADING → SUCCESS
                                              ↘ FAILED
```

Poll `GET /jobs/{id}` until `status` is `SUCCESS` or `FAILED`. Typical processing takes 1–30 seconds depending on file size and preset.

---

## Webhook

If `webhook_url` is provided, the worker sends a `POST` request to it on job completion (both SUCCESS and FAILED).

**Webhook payload** is identical to `GET /jobs/{id}` response:

```json
{
  "job_id": "...",
  "status": "SUCCESS",
  "output_url": "https://...",
  ...
}
```

> Webhook URL must start with `https://`.

---

## Presets

### 1. `transcode_h264_mp4`

Convert any video to H.264 video + AAC audio, MP4 container. Good for compatibility.

| Option | Type | Default | Description |
|---|---|---|---|
| `crf` | int | `23` | Quality factor (0=lossless, 51=worst). Lower = better quality + larger file |
| `audio_bitrate` | string | `"128k"` | Audio bitrate (e.g. `"192k"`, `"64k"`) |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "transcode_h264_mp4",
    "input_url": "https://example.com/video.avi",
    "output_filename": "output.mp4",
    "preset_options": {
      "crf": 20,
      "audio_bitrate": "192k"
    }
  }'
```

---

### 2. `scale_fit_max`

Scale video down to fit within maximum dimensions, preserving aspect ratio. Upscaling is never applied.

| Option | Type | Default | Description |
|---|---|---|---|
| `max_width` | int | `1920` | Maximum output width in pixels |
| `max_height` | int | `1080` | Maximum output height in pixels |
| `crf` | int | `23` | Quality factor |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "scale_fit_max",
    "input_url": "https://example.com/4k-video.mp4",
    "output_filename": "1080p.mp4",
    "preset_options": {
      "max_width": 1920,
      "max_height": 1080
    }
  }'
```

---

### 3. `thumbnail_jpg`

Extract a single frame from the video as a JPEG image.

| Option | Type | Default | Description |
|---|---|---|---|
| `at_seconds` | float | `1.0` | Timestamp (in seconds) to grab the frame from |
| `quality` | int | `5` | JPEG quality scale (`1`=best, `31`=worst) |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "thumbnail_jpg",
    "input_url": "https://example.com/video.mp4",
    "output_filename": "thumb.jpg",
    "preset_options": {
      "at_seconds": 5.0,
      "quality": 3
    }
  }'
```

---

### 4. `burn_subs`

Burn subtitles (SRT or ASS/SSA format) permanently into the video. Requires an additional subtitle file URL.

| Option | Type | Default | Description |
|---|---|---|---|
| `crf` | int | `23` | Quality factor |
| **`input_subs_url`** | string | — | ⚠️ **Required.** HTTPS URL of the `.srt` or `.ass` subtitle file |

`input_subs_url` is passed inside `preset_options`:

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "burn_subs",
    "input_url": "https://example.com/video.mp4",
    "output_filename": "with-subs.mp4",
    "preset_options": {
      "input_subs_url": "https://example.com/subtitles.srt",
      "crf": 22
    }
  }'
```

---

### 5. `overlay_image`

Overlay a PNG or JPG image (watermark, logo) on top of the video at a given position.

| Option | Type | Default | Description |
|---|---|---|---|
| `x` | int | `10` | X offset of the overlay image in pixels |
| `y` | int | `10` | Y offset of the overlay image in pixels |
| `crf` | int | `23` | Quality factor |
| **`input_overlay_url`** | string | — | ⚠️ **Required.** HTTPS URL of the overlay image (PNG recommended) |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "overlay_image",
    "input_url": "https://example.com/video.mp4",
    "output_filename": "watermarked.mp4",
    "preset_options": {
      "input_overlay_url": "https://example.com/logo.png",
      "x": 20,
      "y": 20
    }
  }'
```

---

### 6. `extract_audio_mp3`

Strip video track and save the audio as MP3.

| Option | Type | Default | Description |
|---|---|---|---|
| `audio_bitrate` | string | `"128k"` | MP3 bitrate (e.g. `"192k"`, `"320k"`) |

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "extract_audio_mp3",
    "input_url": "https://example.com/video.mp4",
    "output_filename": "audio.mp3",
    "preset_options": {
      "audio_bitrate": "192k"
    }
  }'
```

---

### 7. `concat_videos`

Concatenate multiple video files into one. Uses FFmpeg stream copy (no re-encoding) — files must have the same codec, resolution and frame rate.

| Option | Type | Default | Description |
|---|---|---|---|
| **`input_urls`** | array of strings | — | ⚠️ **Required.** List of HTTPS URLs to concatenate, **in order** |

`input_url` in the main body is the **first** video; additional videos go in `preset_options.input_urls`.

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "concat_videos",
    "input_url": "https://example.com/part1.mp4",
    "output_filename": "full.mp4",
    "preset_options": {
      "input_urls": [
        "https://example.com/part2.mp4",
        "https://example.com/part3.mp4"
      ]
    }
  }'
```

---

### 8. `hls_package`

Package a video as HLS (HTTP Live Streaming): produces an `index.m3u8` playlist and `.ts` segments, zipped into a single archive.

| Option | Type | Default | Description |
|---|---|---|---|
| `hls_time` | int | `6` | Segment duration in seconds |
| `video_bitrate` | string | `"1000k"` | Target video bitrate |

Output is a `.zip` file containing `index.m3u8` and all segment files.

```bash
curl -X POST https://ffmpeg-api.kuprino.com/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "hls_package",
    "input_url": "https://example.com/video.mp4",
    "output_filename": "hls.zip",
    "preset_options": {
      "hls_time": 4,
      "video_bitrate": "800k"
    }
  }'
```

---

## Error Handling

| HTTP status | Meaning |
|---|---|
| `202` | Job accepted and queued |
| `400` | Bad request — invalid preset, unsafe URL, bad filename |
| `404` | Job not found |
| `422` | Validation error — missing required fields |

When a job fails during processing, `status` is `FAILED` and `error` contains a description.

**Unsafe URLs** — the service blocks requests to private IP ranges (RFC-1918) to prevent SSRF:
- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`

---

## Using from n8n

1. Use an **HTTP Request** node with method `POST`, URL `https://ffmpeg-api.kuprino.com/jobs`, body type JSON.
2. Get back `job_id`.
3. Poll with a **Wait** node + loop, or configure `webhook_url` to point at an n8n Webhook node to receive the result automatically.

**Poll pattern:**
```
HTTP Request (POST /jobs) → Set (save job_id) → Wait 5s → HTTP Request (GET /jobs/{{job_id}}) → IF status=SUCCESS → continue
                                                                                                  ↘ IF status=FAILED → handle error
                                                                                                  ↘ else → back to Wait
```

**Webhook pattern:**
```
HTTP Request (POST /jobs, webhook_url=https://your-n8n.com/webhook/ffmpeg-done) → [wait for webhook trigger]
Webhook node receives full job result → continue
```

---

## Security

- `input_url` must use **HTTPS**
- Private/internal IP ranges are blocked (SSRF protection)
- `output_filename` allows only `[a-zA-Z0-9_\-. ]` characters
- Service runs behind Cloudflare Tunnel — no open ports on the server
