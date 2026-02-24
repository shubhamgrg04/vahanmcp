#!/bin/bash
set -e

PROJECT_DIR="/home/shubham/vahandata"

echo "Deploying updates to $PROJECT_DIR..."

cd $PROJECT_DIR

# Pull latest changes
git pull origin main

# Install dependencies
.venv/bin/pip install -r requirements.txt

# Restart the service
sudo systemctl restart vahan-mcp

echo "Deployment finished successfully!"
