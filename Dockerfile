FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    cpulimit \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create work directory for FFmpeg temp files
RUN mkdir -p /work

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
