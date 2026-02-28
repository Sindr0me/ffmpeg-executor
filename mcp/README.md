# FFmpeg Executor MCP Server

Model Context Protocol (MCP) server that exposes the ffmpeg-executor API as tools for LLM clients like Claude, Cursor, etc.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Via Claude Desktop

Add to `~/.claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ffmpeg-executor": {
      "command": "python",
      "args": ["/path/to/mcp/server.py"],
      "env": {
        "FFMPEG_API_BASE": "https://ffmpeg-api.kuprino.com"
      }
    }
  }
}
```

Then restart Claude Desktop and the tools will be available.

### Via Cursor

Similar setup in Cursor's MCP settings. Use the command:
```
python /path/to/mcp/server.py
```

### Via Any MCP Client

Run the server directly:

```bash
export FFMPEG_API_BASE="https://ffmpeg-api.kuprino.com"
python server.py
```

The server reads JSON-RPC messages from stdin and writes responses to stdout.

## Available Tools

### ffmpeg_run_command

Run a raw FFmpeg command with custom input/output file handling.

**Example:**
```json
{
  "ffmpeg_command": "-i {{in_video}} -vf scale=1280:720 -c:v libx264 -crf 23 {{out_thumb}}",
  "input_files": {
    "in_video": "https://example.com/video.mp4"
  },
  "output_files": {
    "out_thumb": "thumbnail.jpg"
  },
  "wait": true
}
```

Input file aliases must start with `in_` and output aliases must start with `out_`.

### ffmpeg_run_preset

Run a pre-defined video processing preset.

**Available presets:**
- `transcode_h264_mp4`: Convert to H.264 MP4
- `scale_fit_max`: Scale down to max dimensions
- `thumbnail_jpg`: Extract frame as JPEG
- `burn_subs`: Burn subtitles into video
- `overlay_image`: Overlay watermark/logo
- `extract_audio_mp3`: Extract audio as MP3
- `concat_videos`: Concatenate videos
- `hls_package`: Package as HLS ZIP

### ffmpeg_get_command / ffmpeg_get_job

Poll the status of a previously submitted command or job.

### ffmpeg_health

Check if the ffmpeg-executor service is healthy.

## Configuration

Set the `FFMPEG_API_BASE` environment variable to point to your ffmpeg-executor instance.

Default: `https://ffmpeg-api.kuprino.com`

## Security

- All input URLs must be HTTPS
- FFmpeg commands are validated to prevent:
  - Direct network access (must use input_files)
  - Filesystem escapes
  - Dangerous filter features
  - Shell injection
- All private IP ranges are blocked (SSRF protection)
