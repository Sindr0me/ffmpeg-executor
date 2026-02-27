#!/bin/bash
# deploy.sh — initial server setup and deployment script
# Run on a fresh Ubuntu 24.04 Hetzner server as root
set -euo pipefail

REPO_URL="${1:-https://github.com/YOUR_USERNAME/ffmpeg-executor.git}"
APP_DIR="/opt/ffmpeg-executor"

echo "==> Installing Docker..."
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

echo "==> Installing Docker Compose plugin..."
apt-get install -y docker-compose-plugin

echo "==> Creating app directory..."
mkdir -p "$APP_DIR"
cd "$APP_DIR"

echo "==> Cloning repository..."
if [ -d ".git" ]; then
    git pull
else
    git clone "$REPO_URL" .
fi

echo "==> Setting up .env from example..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "!! IMPORTANT: Edit .env before starting services !!"
    echo "   nano $APP_DIR/.env"
    echo ""
fi

echo "==> Creating data directories..."
mkdir -p data/redis data/postgres
mkdir -p /tmp/ffmpeg-work

echo "==> Installing systemd service..."
cp systemd/ffmpeg-executor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ffmpeg-executor

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit configuration:  nano $APP_DIR/.env"
echo "  2. Start services:      systemctl start ffmpeg-executor"
echo "  3. Check logs:          docker compose -f $APP_DIR/docker-compose.yml logs -f"
echo "  4. Health check:        curl http://localhost:8080/health"
