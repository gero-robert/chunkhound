#!/usr/bin/env bash
# Start ChunkHound Memory serve (Streamable HTTP).
# Loads <memory-dir>/memory-serve.env if present (strict parser, never sourced).
#
# Precedence: CLI flags > process environment > memory-serve.env > defaults
#
# Usage:
#   ./serve.sh [--dir PATH] [--host HOST] [--port PORT] [--token TOKEN]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

REPO_ROOT="$(resolve_repo_root "$SCRIPT_DIR")"

CLI_DIR=""
CLI_HOST=""
CLI_PORT=""
CLI_TOKEN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) CLI_DIR="$2"; shift 2 ;;
    --host) CLI_HOST="$2"; shift 2 ;;
    --port) CLI_PORT="$2"; shift 2 ;;
    --token) CLI_TOKEN="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,12p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# Snapshot process env before file load
PROC_DIR="${CHUNKHOUND_MEMORY_DIR:-}"
PROC_HOST="${CHUNKHOUND_MEMORY_HOST:-}"
PROC_PORT="${CHUNKHOUND_MEMORY_PORT:-}"
PROC_TOKEN="${CHUNKHOUND_MEMORY_TOKEN:-}"

DIR="${CLI_DIR:-$PROC_DIR}"
HOST="${CLI_HOST:-$PROC_HOST}"
PORT="${CLI_PORT:-$PROC_PORT}"
TOKEN="${CLI_TOKEN:-$PROC_TOKEN}"

if [[ -z "$DIR" ]]; then
  read -r -p "Memory directory [~/.chunkhound-memory]: " DIR
  DIR="${DIR:-$HOME/.chunkhound-memory}"
fi
DIR="${DIR/#\~/$HOME}"
DIR="$(cd "$DIR" 2>/dev/null && pwd || echo "$DIR")"

ENV_FILE="$DIR/memory-serve.env"
if [[ -f "$ENV_FILE" ]]; then
  echo "Loading $ENV_FILE (strict allowlist parser)"
  CHUNKHOUND_MEMORY_DIR=""
  CHUNKHOUND_MEMORY_HOST=""
  CHUNKHOUND_MEMORY_PORT=""
  CHUNKHOUND_MEMORY_TOKEN=""
  CHUNKHOUND_MEMORY_PUBLIC_HOST=""
  load_memory_env_file "$ENV_FILE"
  FILE_DIR="${CHUNKHOUND_MEMORY_DIR:-}"
  FILE_HOST="${CHUNKHOUND_MEMORY_HOST:-}"
  FILE_PORT="${CHUNKHOUND_MEMORY_PORT:-}"
  FILE_TOKEN="${CHUNKHOUND_MEMORY_TOKEN:-}"

  # CLI > process env > file > (keep current DIR from prompt if set)
  if [[ -n "$CLI_DIR" ]]; then
    DIR="$CLI_DIR"
    DIR="${DIR/#\~/$HOME}"
    DIR="$(cd "$DIR" 2>/dev/null && pwd || echo "$DIR")"
  elif [[ -n "$PROC_DIR" ]]; then
    DIR="$PROC_DIR"
    DIR="${DIR/#\~/$HOME}"
    DIR="$(cd "$DIR" 2>/dev/null && pwd || echo "$DIR")"
  elif [[ -n "$FILE_DIR" ]]; then
    DIR="$FILE_DIR"
    DIR="${DIR/#\~/$HOME}"
    DIR="$(cd "$DIR" 2>/dev/null && pwd || echo "$DIR")"
  fi

  if [[ -n "$CLI_HOST" ]]; then
    HOST="$CLI_HOST"
  elif [[ -n "$PROC_HOST" ]]; then
    HOST="$PROC_HOST"
  elif [[ -n "$FILE_HOST" ]]; then
    HOST="$FILE_HOST"
  fi

  if [[ -n "$CLI_PORT" ]]; then
    PORT="$CLI_PORT"
  elif [[ -n "$PROC_PORT" ]]; then
    PORT="$PROC_PORT"
  elif [[ -n "$FILE_PORT" ]]; then
    PORT="$FILE_PORT"
  fi

  if [[ -n "$CLI_TOKEN" ]]; then
    TOKEN="$CLI_TOKEN"
  elif [[ -n "$PROC_TOKEN" ]]; then
    TOKEN="$PROC_TOKEN"
  elif [[ -n "$FILE_TOKEN" ]]; then
    TOKEN="$FILE_TOKEN"
  fi
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
validate_port "$PORT"

if [[ ! -f "$DIR/.chunkhound.json" ]]; then
  echo "ERROR: $DIR is not initialized. Run docs/memory/setup.sh first." >&2
  exit 1
fi

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: No token. Set CHUNKHOUND_MEMORY_TOKEN, pass --token, or run setup.sh." >&2
  echo "Refusing to mint a random token here (would not match existing clients)." >&2
  exit 1
fi

# Prefer env over argv so the secret is not visible in process listings.
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

# Token comes from env (CHUNKHOUND_MEMORY_TOKEN); omit --token on argv.
exec uv run chunkhound memory serve \
  --dir "$DIR" \
  --host "$HOST" \
  --port "$PORT"
