#!/usr/bin/env bash
# Start ChunkHound Memory serve (Streamable HTTP).
# Loads <memory-dir>/memory-serve.env if present (from setup.sh).
#
# Usage:
#   ./serve.sh [--dir PATH] [--host HOST] [--port PORT] [--token TOKEN]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DIR="${CHUNKHOUND_MEMORY_DIR:-}"
HOST="${CHUNKHOUND_MEMORY_HOST:-0.0.0.0}"
PORT="${CHUNKHOUND_MEMORY_PORT:-8765}"
TOKEN="${CHUNKHOUND_MEMORY_TOKEN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,8p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$DIR" ]]; then
  read -r -p "Memory directory [~/.chunkhound-memory]: " DIR
  DIR="${DIR:-$HOME/.chunkhound-memory}"
fi
DIR="${DIR/#\~/$HOME}"
DIR="$(cd "$DIR" 2>/dev/null && pwd || echo "$DIR")"

ENV_FILE="$DIR/memory-serve.env"
if [[ -f "$ENV_FILE" ]]; then
  echo "Loading $ENV_FILE"
  # shellcheck disable=SC1090
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
  DIR="${CHUNKHOUND_MEMORY_DIR:-$DIR}"
  HOST="${CHUNKHOUND_MEMORY_HOST:-$HOST}"
  PORT="${CHUNKHOUND_MEMORY_PORT:-$PORT}"
  TOKEN="${CHUNKHOUND_MEMORY_TOKEN:-$TOKEN}"
fi

if [[ ! -f "$DIR/.chunkhound.json" ]]; then
  echo "ERROR: $DIR is not initialized. Run docs/memory/setup.sh first." >&2
  exit 1
fi

if [[ -z "$TOKEN" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    TOKEN="$(openssl rand -hex 32)"
  else
    TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  echo "Generated session token (save for clients):"
  echo "  $TOKEN"
fi

export CHUNKHOUND_MEMORY_DIR="$DIR"
export CHUNKHOUND_MEMORY_TOKEN="$TOKEN"
export CHUNKHOUND_MEMORY_HOST="$HOST"
export CHUNKHOUND_MEMORY_PORT="$PORT"

cd "$REPO_ROOT"

echo "Starting memory serve"
echo "  dir:   $DIR"
echo "  bind:  ${HOST}:${PORT}"
echo "  mcp:   http://<this-host-or-lan-ip>:${PORT}/mcp"
echo ""
echo "Press Ctrl+C to stop."
echo ""

exec uv run chunkhound memory serve \
  --dir "$DIR" \
  --host "$HOST" \
  --port "$PORT" \
  --token "$TOKEN"
