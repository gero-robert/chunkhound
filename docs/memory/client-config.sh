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

cat <<EOF

# ============================================================
# ChunkHound Memory — client configs
# URL: ${URL}
# ============================================================

## Generic (Cursor, VS Code, Grok Build, many others)

\`\`\`json
{
  "mcpServers": {
    "chunkhound-memory": {
      "url": "${URL}",
      "headers": {
        "Authorization": "Bearer ${TOKEN}"
      }
    }
  }
}
\`\`\`

Some clients want an explicit transport type:

\`\`\`json
{
  "mcpServers": {
    "chunkhound-memory": {
      "type": "http",
      "url": "${URL}",
      "headers": {
        "Authorization": "Bearer ${TOKEN}"
      }
    }
  }
}
\`\`\`

## Claude Code (CLI)

\`\`\`bash
claude mcp add --transport http -s user chunkhound-memory \\
  ${URL} \\
  --header "Authorization: Bearer ${TOKEN}"
\`\`\`

## Claude Desktop / Cowork (UI)

1. Settings → Integrations / MCP / Connectors → Add custom
2. Name: chunkhound-memory
3. URL: ${URL}
4. Header Authorization: Bearer ${TOKEN}
   (or X-ChunkHound-Token: ${TOKEN})

Config file alternative (paths vary by OS):

\`\`\`json
{
  "mcpServers": {
    "chunkhound-memory": {
      "url": "${URL}",
      "headers": {
        "Authorization": "Bearer ${TOKEN}"
      }
    }
  }
}
\`\`\`

## Health check

\`\`\`bash
curl -s "http://${HOST:-127.0.0.1}:${PORT}/health"
\`\`\`

## Manual steps you still do

- Paste the JSON into each harness's MCP settings (or run the Claude Code command).
- On other machines, use this machine's LAN IP, not 127.0.0.1.
- Open firewall TCP ${PORT} if clients are remote.
- Do not run \`chunkhound memory mcp\` against the same dir while serve is up.

Full guide: docs/memory-setup.md
EOF
