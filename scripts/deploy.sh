#!/bin/bash
set -e

PROJECT_DIR="/root/vahandata"

echo "Deploying updates to $PROJECT_DIR..."

cd $PROJECT_DIR

# Pull latest changes
git pull origin main

# Install system dependencies if missing (required for venv on Ubuntu/Debian)
if ! dpkg -l | grep -q "python3-venv"; then
  echo "Installing python3-venv..."
  apt-get update
  apt-get install -y python3-venv
fi

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# Install dependencies
.venv/bin/pip install -r requirements.txt

# Setup systemd service if missing
if [ ! -f "/etc/systemd/system/vahan-mcp.service" ]; then
  cp $PROJECT_DIR/vahan-mcp.service /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable vahan-mcp
fi

# Restart the service
systemctl restart vahan-mcp

echo "Deployment finished successfully!"
