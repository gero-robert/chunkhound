#!/usr/bin/env bash
# Print MCP client config snippets for ChunkHound Memory.
# Usage:
#   ./client-config.sh [--host HOST] [--port PORT] [--token TOKEN] [--url URL]
set -euo pipefail

HOST="${CHUNKHOUND_MEMORY_PUBLIC_HOST:-}"
PORT="${CHUNKHOUND_MEMORY_PORT:-8765}"
TOKEN="${CHUNKHOUND_MEMORY_TOKEN:-}"
URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,6p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  read -r -p "Bearer token: " TOKEN
fi
if [[ -z "$TOKEN" ]]; then
  echo "Token is required." >&2
  exit 1
fi

if [[ -z "$URL" ]]; then
  if [[ -z "$HOST" ]]; then
    read -r -p "Public host/IP clients will use [127.0.0.1]: " HOST
    HOST="${HOST:-127.0.0.1}"
  fi
  URL="http://${HOST}:${PORT}/mcp"
fi

# JSON-escape via Python so tokens with quotes/backslashes stay valid
export _CC_URL="$URL"
export _CC_TOKEN="$TOKEN"
export _CC_HOST="${HOST:-127.0.0.1}"
export _CC_PORT="$PORT"

python3 <<'PY'
import json, os, shlex

url = os.environ["_CC_URL"]
token = os.environ["_CC_TOKEN"]
host = os.environ["_CC_HOST"]
port = os.environ["_CC_PORT"]

cfg = {
    "mcpServers": {
        "chunkhound-memory": {
            "url": url,
            "headers": {"Authorization": f"Bearer {token}"},
        }
    }
}
cfg_typed = {
    "mcpServers": {
        "chunkhound-memory": {
            "type": "http",
            "url": url,
            "headers": {"Authorization": f"Bearer {token}"},
        }
    }
}
json_block = json.dumps(cfg, indent=2)
json_typed = json.dumps(cfg_typed, indent=2)
# Safe for pasting into shell double-quoted --header value
header_val = f"Authorization: Bearer {token}"
claude_cmd = (
    "claude mcp add --transport http -s user chunkhound-memory \\\n"
    f"  {shlex.quote(url)} \\\n"
    f"  --header {shlex.quote(header_val)}"
)
health_url = f"http://{host}:{port}/health"

print(f"""
# ============================================================
# ChunkHound Memory — client configs
# URL: {url}
# ============================================================

## Generic (Cursor, VS Code, Grok Build, many others)

```json
{json_block}
```

Some clients want an explicit transport type:

```json
{json_typed}
```

## Claude Code (CLI)

```bash
{claude_cmd}
```

## Claude Desktop / Cowork (UI)

1. Settings → Integrations / MCP / Connectors → Add custom
2. Name: chunkhound-memory
3. URL: {url}
4. Header Authorization: Bearer <token>
   (or X-ChunkHound-Token: <token>)

Config file alternative (paths vary by OS) — same JSON as Generic above.

## Health check

```bash
curl -s {shlex.quote(health_url)}
```

## Manual steps you still do

- Paste the JSON into each harness's MCP settings (or run the Claude Code command).
- On other machines, use this machine's LAN IP, not 127.0.0.1.
- Open firewall TCP {port} if clients are remote.
- Do not run `chunkhound memory mcp` against the same dir while serve is up.

Full guide: docs/memory-setup.md
""")
PY
