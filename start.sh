#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Start the VAHAN MCP server + cloudflared tunnel
#
# Usage:
#   ./start.sh                    # quick tunnel (random *.trycloudflare.com URL)
#   ./start.sh --named <name>     # named tunnel with stable URL (requires login)
#
# Named tunnel one-time setup:
#   cloudflared tunnel login
#   cloudflared tunnel create vahanmcp
#   cloudflared tunnel route dns vahanmcp vahanmcp.shubhamgrg.com   (optional custom domain)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
SERVER_SCRIPT="$SCRIPT_DIR/mcp_server.py"
HOST=127.0.0.1
PORT=8000

NAMED=""
TUNNEL_NAME="vahanmcp"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --named) TUNNEL_NAME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Start MCP server ──────────────────────────────────────────────────────────
echo "Starting VAHAN MCP server on http://$HOST:$PORT/mcp ..."
"$PYTHON" "$SERVER_SCRIPT" --transport http --host "$HOST" --port "$PORT" &
MCP_PID=$!

# Give the server a moment to bind
sleep 1

if ! kill -0 "$MCP_PID" 2>/dev/null; then
  echo "ERROR: MCP server failed to start (port $PORT already in use?)"
  exit 1
fi

echo "MCP server running (PID $MCP_PID)"

# ── Start cloudflared tunnel ───────────────────────────────────────────────────
cleanup() {
  echo ""
  echo "Shutting down..."
  kill "$MCP_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

if [[ -n "$TUNNEL_NAME" ]]; then
  # Named tunnel — stable URL, requires prior `cloudflared tunnel login` + create
  CREDS_FILE="$HOME/.cloudflared/$TUNNEL_NAME.json"
  CONFIG="$HOME/.cloudflared/$TUNNEL_NAME.yml"

  # cloudflared names credentials files by UUID, not tunnel name.
  # Auto-symlink <uuid>.json → <tunnel-name>.json so the config works.
  if [[ ! -f "$CREDS_FILE" ]]; then
    TUNNEL_ID=$(cloudflared tunnel info "$TUNNEL_NAME" 2>&1 | grep -oE '[0-9a-f-]{36}' | head -1)
    UUID_CREDS="$HOME/.cloudflared/${TUNNEL_ID}.json"
    if [[ -n "$TUNNEL_ID" && -f "$UUID_CREDS" ]]; then
      ln -sf "$UUID_CREDS" "$CREDS_FILE"
      echo "Linked credentials: $UUID_CREDS → $CREDS_FILE"
    else
      echo "ERROR: Could not find credentials for tunnel '$TUNNEL_NAME'."
      echo "Run:  cloudflared tunnel login && cloudflared tunnel create $TUNNEL_NAME"
      kill "$MCP_PID" 2>/dev/null || true
      exit 1
    fi
  fi

  if [[ ! -f "$CONFIG" ]]; then
    cat > "$CONFIG" <<EOF
tunnel: $TUNNEL_NAME
credentials-file: $CREDS_FILE

ingress:
  - service: http://localhost:$PORT
EOF
    echo "Created tunnel config: $CONFIG"
  fi
  echo "Starting named tunnel '$TUNNEL_NAME' ..."
  cloudflared tunnel --config "$CONFIG" run "$TUNNEL_NAME"
else
  # Quick tunnel — random *.trycloudflare.com URL, no login needed
  echo "Starting quick tunnel (no login required) ..."
  echo "The public URL will appear below — share it with mcp-remote clients."
  echo ""
  cloudflared tunnel --url "http://$HOST:$PORT"
fi

cleanup
