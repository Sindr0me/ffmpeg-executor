# FFmpeg Executor — MCP Server Setup

This directory contains an [MCP](https://modelcontextprotocol.io) server that exposes the **ffmpeg-executor** API as tools for Claude and any other LLM client that supports the Model Context Protocol.

Once connected, you can ask your AI assistant to process video directly in natural language — no API calls needed manually.

---

## What you get

5 tools become available to the AI:

| Tool | Description |
|---|---|
| `ffmpeg_run_preset` | Run a ready-made preset (thumbnail, transcode, crop, etc.) |
| `ffmpeg_run_command` | Run any raw FFmpeg command (full flexibility) |
| `ffmpeg_get_job` | Check preset job status by ID |
| `ffmpeg_get_command` | Check command status by ID |
| `ffmpeg_health` | Check if the service is healthy |

---

## Requirements

- **Python 3.11+**
- Internet access to `https://ffmpeg-api.kuprino.com`

The server uses [PEP 723 inline script metadata](https://packaging.python.org/en/latest/specifications/inline-script-metadata/) — dependencies (`mcp`, `httpx`, `pydantic`) are installed **automatically** when you use `uv run`. No manual `pip install` needed.

---

## Option A — Claude Desktop

### Step 1 — Download the server

```bash
curl -o ~/ffmpeg_executor_mcp.py \
  https://raw.githubusercontent.com/Sindr0me/ffmpeg-executor/main/mcp/server.py
```

Or clone the repo and use the file directly from `mcp/server.py`.

### Step 2 — Install `uv` (if not already installed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> `uv` is a fast Python package manager. The MCP server uses it to auto-install its own dependencies.

### Step 3 — Edit Claude Desktop config

Open the config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Add the following inside `"mcpServers"`:

```json
{
  "mcpServers": {
    "ffmpeg-executor": {
      "command": "uv",
      "args": [
        "run",
        "--with", "mcp[cli]",
        "--with", "httpx",
        "--with", "pydantic",
        "/path/to/ffmpeg_executor_mcp.py"
      ],
      "env": {
        "FFMPEG_API_BASE": "https://ffmpeg-api.kuprino.com"
      }
    }
  }
}
```

> Replace `/path/to/ffmpeg_executor_mcp.py` with the actual path from Step 1, e.g. `/Users/yourname/ffmpeg_executor_mcp.py`

**Alternatively**, if you don't want to use `uv`, install dependencies manually and use `python3`:

```bash
pip install "mcp[cli]" httpx pydantic
```

```json
{
  "mcpServers": {
    "ffmpeg-executor": {
      "command": "python3",
      "args": ["/path/to/ffmpeg_executor_mcp.py"],
      "env": {
        "FFMPEG_API_BASE": "https://ffmpeg-api.kuprino.com"
      }
    }
  }
}
```

### Step 4 — Restart Claude Desktop

Fully quit Claude Desktop (Cmd+Q / Alt+F4) and reopen it. The ffmpeg tools will appear when you start a new conversation.

### Verify it's working

Ask Claude:
```
Check the ffmpeg executor health
```

Claude should call `ffmpeg_health` and reply with `{"status": "ok"}`.

---

## Option B — Cursor

Create or edit `.cursor/mcp.json` in your project root (or `~/.cursor/mcp.json` for global config):

```json
{
  "mcpServers": {
    "ffmpeg-executor": {
      "command": "uv",
      "args": [
        "run",
        "/path/to/mcp/server.py"
      ],
      "env": {
        "FFMPEG_API_BASE": "https://ffmpeg-api.kuprino.com"
      }
    }
  }
}
```

Restart Cursor. The tools will be available in Agent mode.

---

## Option C — Windsurf

Open **Settings → MCP → Add Server** and paste:

```json
{
  "ffmpeg-executor": {
    "command": "uv",
    "args": ["run", "/path/to/mcp/server.py"],
    "env": {
      "FFMPEG_API_BASE": "https://ffmpeg-api.kuprino.com"
    }
  }
}
```

---

## Option D — Claude Code (CLI)

```bash
claude mcp add ffmpeg-executor \
  -e FFMPEG_API_BASE=https://ffmpeg-api.kuprino.com \
  -- uv run /path/to/mcp/server.py
```

Or add to `~/.claude/claude.json` manually under `mcpServers`.

---

## Option E — Any MCP-compatible client

Any client that speaks the [MCP stdio transport](https://modelcontextprotocol.io/docs/concepts/transports) can use this server.

Start the server process:

```bash
FFMPEG_API_BASE=https://ffmpeg-api.kuprino.com uv run /path/to/server.py
```

The server communicates over **stdin/stdout** using JSON-RPC (MCP stdio protocol).

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `FFMPEG_API_BASE` | `https://ffmpeg-api.kuprino.com` | Override to point at a self-hosted instance |

---

## Available Presets (20 total)

Once connected, you can ask the AI to use any of these presets:

### BASIC
| Preset | What it does |
|---|---|
| `transcode_h264_mp4` | Convert any video to H.264 + AAC MP4 |
| `scale_fit_max` | Scale down to fit within max dimensions (default 1920×1080) |
| `thumbnail_jpg` | Extract a frame as JPEG (default at 1 second) |
| `extract_audio_mp3` | Strip and export the audio track as MP3 |

### OVERLAYS
| Preset | What it does |
|---|---|
| `burn_subs` | Burn SRT/ASS subtitles permanently into the video |
| `overlay_image` | Add a watermark or logo at a given position |
| `add_text` | Burn a text caption or CTA into the video |

### SOCIAL MEDIA / UGC
| Preset | What it does |
|---|---|
| `crop_to_aspect` | Center-crop to 9:16 / 4:5 / 1:1 / 16:9 |
| `trim` | Cut a clip by start/end time or duration |
| `speed_change` | Slow-motion (0.5×) or fast-forward (2–4×) |
| `fade` | Fade-in and/or fade-out for video and audio |
| `gif_export` | Export a short clip as an optimized GIF |

### AUDIO
| Preset | What it does |
|---|---|
| `normalize_loudness` | EBU R128 loudness normalization (target LUFS) |
| `mix_audio` | Mix background music under the original audio |
| `replace_audio` | Replace the audio track with a TTS/voiceover file |
| `clear_silence` | Auto-detect and remove silent segments |

### DELIVERY
| Preset | What it does |
|---|---|
| `concat_videos` | Join multiple video files in sequence |
| `hls_package` | Package as HLS playlist + segments (outputs a .zip) |
| `resize_for_platform` | One-click encode for TikTok, Instagram, YouTube, etc. |
| `compress_video` | Reduce file size by CRF or target MB (2-pass) |

---

## Example prompts

After connecting the MCP server, you can use natural language:

```
Сделай превью из этого видео на 5-й секунде:
https://example.com/video.mp4
```

```
Обрежь видео до вертикального формата 9:16 для TikTok
и добавь текст "Shop Now!" внизу:
https://example.com/ad.mp4
```

```
Сожми это видео до 50 МБ:
https://example.com/big_video.mp4
```

```
Замени аудиодорожку на этот TTS-файл:
video: https://example.com/video.mp4
audio: https://example.com/voiceover.mp3
```

```
Подготовь видео для публикации в Instagram Reels
(кроп, платформенные настройки, нормализация звука):
https://example.com/raw.mp4
```

---

## Troubleshooting

**Tools don't appear in Claude Desktop**
- Make sure you fully quit and reopened Claude Desktop after editing the config
- Check the config file is valid JSON (no trailing commas, matching brackets)
- On macOS, verify the path in `args` with `ls -la /path/to/server.py`

**`uv: command not found`**
- Run `curl -LsSf https://astral.sh/uv/install.sh | sh` and restart your terminal
- Or use `python3` directly after running `pip install "mcp[cli]" httpx pydantic`

**`ModuleNotFoundError: No module named 'mcp'`**
- Switch to the `uv run` approach, or run `pip install "mcp[cli]" httpx pydantic`

**Connection errors / timeout**
- Check `ffmpeg_health` tool — if it fails, the API may be temporarily unavailable
- Verify `FFMPEG_API_BASE` is set correctly
- The server at `https://ffmpeg-api.kuprino.com` requires internet access

**Job returns FAILED**
- The `error` field in the response will explain the cause
- Most common reasons: unsupported input format, preset option out of range, or SSRF-blocked URL (only public HTTPS URLs are accepted)
