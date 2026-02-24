#!/bin/bash
set -e

PROJECT_DIR="/root/vahandata"

echo "Deploying updates to $PROJECT_DIR..."

cd $PROJECT_DIR

# Pull latest changes
git pull origin main

# Install system dependencies if missing
if ! dpkg -l | grep -q "python3-venv"; then
  echo "Installing python3-venv..."
  apt-get update && apt-get install -y python3-venv
fi

if ! dpkg -l | grep -q "nginx"; then
  echo "Installing nginx..."
  apt-get update && apt-get install -y nginx
fi

if ! dpkg -l | grep -q "certbot"; then
  echo "Installing certbot..."
  apt-get update && apt-get install -y certbot python3-certbot-nginx
fi

# Check if virtual environment exists and is healthy
if [ ! -f ".venv/bin/python3" ]; then
  echo "Virtual environment missing or broken. Creating..."
  rm -rf .venv
  python3 -m venv .venv
fi

# Install dependencies
.venv/bin/pip install -r requirements.txt

# Setup systemd service
cp $PROJECT_DIR/vahan-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable vahan-mcp
systemctl restart vahan-mcp

# Setup Nginx
cp $PROJECT_DIR/vahan-mcp.nginx /etc/nginx/sites-available/vahanmcp.shubhamgrg.com
if [ ! -f "/etc/nginx/sites-enabled/vahanmcp.shubhamgrg.com" ]; then
    ln -s /etc/nginx/sites-available/vahanmcp.shubhamgrg.com /etc/nginx/sites-enabled/
fi
nginx -t && systemctl reload nginx

# Setup SSL (if not already present for this domain)
if [ ! -d "/etc/letsencrypt/live/vahanmcp.shubhamgrg.com" ]; then
    echo "Running Certbot to get SSL certificate..."
    certbot --nginx -d vahanmcp.shubhamgrg.com --non-interactive --agree-tos --email shubham@gmail.com # Replace with real email if desired
fi

echo "Deployment finished successfully!"
