#!/usr/bin/env bash
# Interactive setup for ChunkHound Memory (init + embeddings + env + client configs).
# Usage: ./setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

REPO_ROOT="$(resolve_repo_root "$SCRIPT_DIR")"

prompt() {
  local msg="$1"
  local default="${2:-}"
  local value=""
  if [[ -n "$default" ]]; then
    read -r -p "$msg [$default]: " value
    echo "${value:-$default}"
  else
    read -r -p "$msg: " value
    echo "$value"
  fi
}

prompt_secret() {
  local msg="$1"
  local value=""
  read -r -s -p "$msg: " value
  echo "" >&2
  echo "$value"
}

yes_no() {
  local msg="$1"
  local default="${2:-y}"
  local yn
  read -r -p "$msg [y/n] (default $default): " yn
  yn="${yn:-$default}"
  [[ "$yn" =~ ^[Yy] ]]
}

echo "============================================================"
echo " ChunkHound Memory — interactive setup"
echo "============================================================"
echo "Repo: $REPO_ROOT"
echo ""

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: 'uv' not found. Install: https://docs.astral.sh/uv/"
  echo "Manual: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

DEFAULT_DIR="$HOME/.chunkhound-memory"
MEMORY_DIR="$(prompt "Memory directory" "$DEFAULT_DIR")"
MEMORY_DIR="${MEMORY_DIR/#\~/$HOME}"
mkdir -p "$MEMORY_DIR"
MEMORY_DIR="$(cd "$MEMORY_DIR" && pwd)"

echo ""
echo "→ Initializing memory directory..."
cd "$REPO_ROOT"
uv run chunkhound memory init --dir "$MEMORY_DIR"

CONFIG="$MEMORY_DIR/.chunkhound.json"
if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: expected $CONFIG after init."
  exit 1
fi

echo ""
echo "Embedding provider (required for semantic recall)"
echo "  1) openai"
echo "  2) voyageai"
echo "  3) skip (configure $CONFIG manually later)"
PROVIDER_CHOICE="$(prompt "Choice" "1")"
case "$PROVIDER_CHOICE" in
  1|openai) PROVIDER="openai" ;;
  2|voyageai) PROVIDER="voyageai" ;;
  3|skip|"") PROVIDER="" ;;
  *) PROVIDER="$PROVIDER_CHOICE" ;;
esac

if [[ -n "$PROVIDER" ]]; then
  MODEL_DEFAULT="text-embedding-3-small"
  if [[ "$PROVIDER" == "voyageai" ]]; then
    MODEL_DEFAULT="voyage-3"
  fi
  MODEL="$(prompt "Embedding model" "$MODEL_DEFAULT")"
  API_KEY="$(prompt_secret "API key for $PROVIDER (input hidden)")"
  if [[ -z "$API_KEY" ]]; then
    echo "No API key entered — leaving embedding config unchanged."
  else
    # Pass key via env (not argv) so it is not visible in ps
    export CHUNKHOUND_SETUP_EMBED_CONFIG="$CONFIG"
    export CHUNKHOUND_SETUP_EMBED_PROVIDER="$PROVIDER"
    export CHUNKHOUND_SETUP_EMBED_API_KEY="$API_KEY"
    export CHUNKHOUND_SETUP_EMBED_MODEL="$MODEL"
    python3 <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ["CHUNKHOUND_SETUP_EMBED_CONFIG"])
data = json.loads(path.read_text(encoding="utf-8"))
data["embedding"] = {
    "provider": os.environ["CHUNKHOUND_SETUP_EMBED_PROVIDER"],
    "api_key": os.environ["CHUNKHOUND_SETUP_EMBED_API_KEY"],
    "model": os.environ["CHUNKHOUND_SETUP_EMBED_MODEL"],
}
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"Updated embedding provider={data['embedding']['provider']} "
      f"model={data['embedding']['model']} in {path}")
PY
    unset CHUNKHOUND_SETUP_EMBED_API_KEY CHUNKHOUND_SETUP_EMBED_PROVIDER
    unset CHUNKHOUND_SETUP_EMBED_MODEL CHUNKHOUND_SETUP_EMBED_CONFIG
    restrict_file_mode "$CONFIG"
  fi
else
  echo ""
  echo "MANUAL: edit $CONFIG and add an \"embedding\" block, then re-index."
fi

echo ""
if yes_no "Re-index memory directory with embeddings now?" "y"; then
  echo "→ Indexing (may take a minute)..."
  if ! uv run chunkhound index "$MEMORY_DIR" \
    --config "$CONFIG" \
    --db "$MEMORY_DIR/.chunkhound/db"; then
    echo "Index failed. Fix embeddings/config and run:"
    echo "  uv run chunkhound index \"$MEMORY_DIR\" --config \"$CONFIG\" --db \"$MEMORY_DIR/.chunkhound/db\""
  fi
else
  echo "MANUAL later:"
  echo "  uv run chunkhound index \"$MEMORY_DIR\" --config \"$CONFIG\" --db \"$MEMORY_DIR/.chunkhound/db\""
fi

echo ""
echo "LAN serve settings"
HOST="$(prompt "Bind host (0.0.0.0 = all interfaces)" "0.0.0.0")"
PORT="$(prompt "Port" "8765")"
validate_port "$PORT"

# Reuse existing token from env file or process env by default
EXISTING_TOKEN=""
ENV_FILE="$MEMORY_DIR/memory-serve.env"
if [[ -f "$ENV_FILE" ]]; then
  CHUNKHOUND_MEMORY_TOKEN=""
  load_memory_env_file "$ENV_FILE"
  EXISTING_TOKEN="${CHUNKHOUND_MEMORY_TOKEN:-}"
fi
if [[ -z "$EXISTING_TOKEN" && -n "${CHUNKHOUND_MEMORY_TOKEN:-}" ]]; then
  EXISTING_TOKEN="$CHUNKHOUND_MEMORY_TOKEN"
fi

if [[ -n "$EXISTING_TOKEN" ]]; then
  echo "Found existing token in env file or environment."
  if yes_no "Reuse existing token? (n = generate/rotate — breaks existing clients)" "y"; then
    TOKEN="$EXISTING_TOKEN"
    echo "Reusing existing token."
  else
    echo "WARNING: Rotating the token invalidates every harness config using the old one."
    if yes_no "Generate a new random token?" "y"; then
      if command -v openssl >/dev/null 2>&1; then
        TOKEN="$(openssl rand -hex 32)"
      else
        TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
      fi
      echo "Generated token (store safely; shown once here):"
      echo "  $TOKEN"
    else
      TOKEN="$(prompt_secret "Enter new token (input hidden)")"
    fi
  fi
elif [[ -n "${CHUNKHOUND_MEMORY_TOKEN:-}" ]]; then
  TOKEN="$CHUNKHOUND_MEMORY_TOKEN"
  echo "Using CHUNKHOUND_MEMORY_TOKEN from environment."
else
  if yes_no "Generate a random API token?" "y"; then
    if command -v openssl >/dev/null 2>&1; then
      TOKEN="$(openssl rand -hex 32)"
    else
      TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    fi
    echo "Generated token (store safely; shown once here):"
    echo "  $TOKEN"
  else
    TOKEN="$(prompt_secret "Enter token (input hidden)")"
  fi
fi

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: empty token not allowed." >&2
  exit 1
fi

PUBLIC_HOST="$(prompt "Hostname/IP other machines should use (for printed configs)" "127.0.0.1")"

CHUNKHOUND_MEMORY_DIR="$MEMORY_DIR"
CHUNKHOUND_MEMORY_TOKEN="$TOKEN"
CHUNKHOUND_MEMORY_HOST="$HOST"
CHUNKHOUND_MEMORY_PORT="$PORT"
CHUNKHOUND_MEMORY_PUBLIC_HOST="$PUBLIC_HOST"
write_memory_env_file "$ENV_FILE"
echo "Wrote $ENV_FILE (mode 600 when supported)"

echo ""
echo "→ Client configuration snippets:"
bash "$SCRIPT_DIR/client-config.sh" --host "$PUBLIC_HOST" --port "$PORT" --token "$TOKEN"

echo ""
echo "============================================================"
echo " Next steps"
echo "============================================================"
echo "1. Start the server on this host:"
echo "     $SCRIPT_DIR/serve.sh --dir \"$MEMORY_DIR\""
echo ""
echo "2. MANUAL — open firewall TCP $PORT if other PCs will connect."
echo ""
echo "3. MANUAL — paste client JSON into each harness (Claude Code, Cursor,"
echo "   Grok Build, etc.) using the snippets printed above."
echo "   Claude Code can use the printed 'claude mcp add' command."
echo ""
echo "4. Do NOT also run 'chunkhound memory mcp' on the same directory while"
echo "   serve is running. All clients (including this PC) use HTTP."
echo ""
echo "5. Optional LLM for better memory_research summaries: edit $CONFIG"
echo "   and add an \"llm\" block (see chunkhound.ai configuration docs)."
echo ""
if yes_no "Start memory serve now?" "n"; then
  exec bash "$SCRIPT_DIR/serve.sh" --dir "$MEMORY_DIR" --host "$HOST" --port "$PORT"
fi

echo "Done. Full guide: docs/memory-setup.md"
