# FFmpeg Executor API

Internal HTTP service for video processing from n8n. Accepts jobs, runs FFmpeg in isolation, stores results in S3, returns a public URL.

## Stack

- **API**: FastAPI + uvicorn
- **Queue**: Celery + Redis
- **DB**: PostgreSQL (job metadata)
- **Storage**: S3-compatible (Cloudflare R2 / AWS S3)
- **Access**: Cloudflare Tunnel + Cloudflare Access

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/ffmpeg-executor.git
cd ffmpeg-executor
cp .env.example .env
nano .env   # fill in DB_PASSWORD, S3_*, CLOUDFLARE_TUNNEL_TOKEN
```

### 2. Start

```bash
docker compose up -d --build
```

### 3. Verify

```bash
curl http://localhost:8080/health
```

## Deploy to Hetzner

```bash
# On the server (as root):
bash <(curl -s https://raw.githubusercontent.com/YOUR_USERNAME/ffmpeg-executor/main/deploy.sh) \
  https://github.com/YOUR_USERNAME/ffmpeg-executor.git
```

After setup, edit `/opt/ffmpeg-executor/.env`, then:

```bash
systemctl start ffmpeg-executor
```

## API

### POST /jobs

Submit a video processing job.

```json
{
  "preset": "transcode_h264_mp4",
  "preset_options": { "crf": 23 },
  "input_url": "https://cdn.example.com/video.mov",
  "output_filename": "result.mp4",
  "webhook_url": "https://n8n.example.com/webhook/abc123",
  "metadata": { "n8n_execution_id": "xyz" }
}
```

Returns: `{ "job_id": "550e8400-..." }`

### GET /jobs/{job_id}

Returns job status and result URL.

Statuses: `QUEUED → DOWNLOADING → PROCESSING → UPLOADING → SUCCESS / FAILED`

### GET /health

Returns `200` if API, Redis and PostgreSQL are all up.

## Presets

| Preset | Description | Key Options |
|---|---|---|
| `transcode_h264_mp4` | Convert to H.264/AAC MP4 | `crf` (18–28), `audio_bitrate` |
| `scale_fit_max` | Scale down to max dimensions | `max_width`, `max_height`, `crf` |
| `thumbnail_jpg` | Extract frame as JPEG | `at_seconds`, `quality` |
| `burn_subs` | Burn SRT/ASS subtitles | `input_subs_url`, `crf` |
| `overlay_image` | Overlay watermark PNG/JPG | `input_overlay_url`, `x`, `y`, `crf` |
| `extract_audio_mp3` | Extract audio as MP3 | `audio_bitrate` |
| `concat_videos` | Concatenate videos (stream copy) | `input_urls` (list) |
| `hls_package` | Package as HLS, output ZIP | `hls_time`, `video_bitrate` |

## Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Architecture

```
n8n Cloud
    │
    │  POST /jobs  (via Cloudflare Tunnel)
    ▼
FastAPI API ──── PostgreSQL (job status)
    │
    │ enqueue
    ▼
  Redis
    │
    │ dequeue
    ▼
Celery Worker
  1. Download input_url
  2. Run FFmpeg (subprocess, timeout)
  3. Upload result → S3
  4. Update DB + send webhook
    │
    │ webhook
    ▼
n8n Cloud
```
