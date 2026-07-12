# ChunkHound Memory — setup & client connection guide

This guide covers installing and running the **shared Memory MCP server**, then connecting common coding harnesses (Claude Code, Claude Desktop / Cowork, Grok Build, Cursor, VS Code, Windsurf, and similar MCP clients).

Agent **usage policy** (when to research, store, approve skills, archive) is **not** configured per client. It is shipped inside the MCP as server `instructions` and tool descriptions. Once a harness is connected, the model receives that policy automatically. For the full policy text, see `MEMORY_PROTOCOL.md` in your memory directory after `chunkhound memory init`, or the source of truth in `chunkhound/services/memory/agent_protocol.py`.

---

## What you are setting up

| Piece | Role |
|-------|------|
| **Memory directory** | Markdown files (preferences, skills, lessons, decisions) + DuckDB index |
| **`chunkhound memory serve`** | One long-lived process that owns the DB and exposes Streamable HTTP |
| **Harness MCP clients** | Claude, Grok, Cursor, etc. — connect over HTTP (or stdio for single-machine debug only) |

**Recommended topology (multi-machine / multi-harness):**

```text
  [Claude Code]  [Cowork]  [Grok Build]  [Cursor]  [VS Code]
         \          |           |           |          /
          \         |           |           |         /
           +--------|---- Streamable HTTP ------------+
                    |    http://HOST:8765/mcp
                    v
         always-on machine: chunkhound memory serve
                    |
              memory directory + DuckDB
```

Rules of thumb:

1. Run **one** `memory serve` per memory directory.
2. Point **every** harness (including on the host machine) at that HTTP URL.
3. Do **not** also run `chunkhound memory mcp` (stdio) against the same directory while serve is running — only one process may own the DuckDB file.

---

## Prerequisites

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- ChunkHound installed from this branch/checkout (or a release that includes Memory MCP):

  ```bash
  # From a clone of this repo (development)
  cd /path/to/chunkhound
  uv sync

  # Or tool install once published with memory features
  uv tool install chunkhound
  ```

- An **embedding provider** configured for the memory directory (required for `memory_research` / `memory_semantic_search`). Optional LLM improves research summaries.
- Network: host reachable from other machines on your LAN if you want multi-computer access; open the chosen TCP port on the host firewall.

---

## 1. Initialize the memory directory

Pick a durable path on the host (examples below). Avoid paths you will delete with temp cleaners.

```bash
# Default: ~/.chunkhound-memory
uv run chunkhound memory init

# Or explicit directory (recommended on a dedicated host)
uv run chunkhound memory init --dir /data/chunkhound-memory
# Windows example:
# uv run chunkhound memory init --dir D:\data\chunkhound-memory
```

This creates:

- `.chunkhound.json` — database + prose indexing config  
- `preferences/`, `skills/`, `lessons/`, `decisions/`, `archive/`  
- `MEMORY_PROTOCOL.md` — human-readable copy of the agent policy  

### Configure embeddings (required for semantic recall)

Edit `<memory-dir>/.chunkhound.json` and add an embedding block, for example:

```json
{
  "database": {
    "path": "/data/chunkhound-memory/.chunkhound/db",
    "provider": "duckdb"
  },
  "indexing": {
    "chunker": "prose",
    "include": ["*.md", "*.txt", "*.html"],
    "index_unknown_files": true,
    "exclude": [".chunkhound/**", "archive/**"]
  },
  "embedding": {
    "provider": "openai",
    "api_key": "YOUR_KEY",
    "model": "text-embedding-3-small"
  }
}
```

Other providers (VoyageAI, local Ollama, etc.) follow the same ChunkHound embedding config patterns as project indexing. See [configuration docs](https://chunkhound.ai/docs/configuration/).

Optional: add an `llm` block if you want richer `memory_research` summaries.

Re-index after configuring embeddings:

```bash
uv run chunkhound index /data/chunkhound-memory \
  --config /data/chunkhound-memory/.chunkhound.json \
  --db /data/chunkhound-memory/.chunkhound/db
```

---

## 2. Start the shared Memory server

On the **always-on host**:

```bash
export CHUNKHOUND_MEMORY_DIR=/data/chunkhound-memory
export CHUNKHOUND_MEMORY_TOKEN="$(openssl rand -hex 32)"   # save this

uv run chunkhound memory serve \
  --dir "$CHUNKHOUND_MEMORY_DIR" \
  --host 0.0.0.0 \
  --port 8765 \
  --token "$CHUNKHOUND_MEMORY_TOKEN"
```

Windows (PowerShell):

```powershell
$env:CHUNKHOUND_MEMORY_DIR = "D:\data\chunkhound-memory"
$env:CHUNKHOUND_MEMORY_TOKEN = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
# Prefer a known secret you store in a password manager, not a one-liner random.

uv run chunkhound memory serve `
  --dir $env:CHUNKHOUND_MEMORY_DIR `
  --host 0.0.0.0 `
  --port 8765 `
  --token $env:CHUNKHOUND_MEMORY_TOKEN
```

On startup the server prints client config snippets. Endpoints:

| Endpoint | Purpose |
|----------|---------|
| `http://HOST:8765/mcp` | Streamable HTTP MCP (authenticated) |
| `http://HOST:8765/health` | Liveness (no auth; no path disclosure) |

Auth (either header):

- `Authorization: Bearer <token>`
- `X-ChunkHound-Token: <token>`

**Host process management:** run under `systemd`, Task Scheduler, `nssm`, or a terminal multiplexer so it survives logouts. Keep the token private on your LAN.

### Same machine as clients

On the host itself, harnesses should still use HTTP:

- `http://127.0.0.1:8765/mcp` (loopback)

Do not start a second `memory mcp` stdio process against the same directory while serve is running.

### Single-machine debug only (stdio)

If you are **not** running `memory serve` and only need one local harness:

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "command": "uv",
      "args": ["run", "chunkhound", "memory", "mcp"],
      "env": {
        "CHUNKHOUND_MEMORY_DIR": "/data/chunkhound-memory"
      }
    }
  }
}
```

For multi-harness or multi-computer use, prefer **serve + HTTP**.

---

## 3. Generic MCP client pattern

Almost all modern harnesses use one of two shapes.

### A. Remote / LAN (recommended) — Streamable HTTP

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "url": "http://MEMORY_HOST:8765/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Some clients use an explicit transport field:

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "type": "http",
      "url": "http://MEMORY_HOST:8765/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Replace:

- `MEMORY_HOST` — LAN IP or DNS name of the serve host (or `127.0.0.1` on the host)
- `YOUR_TOKEN` — same value as `--token` / `CHUNKHOUND_MEMORY_TOKEN`

### B. Local stdio (debug / single client)

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "command": "uv",
      "args": ["run", "chunkhound", "memory", "mcp"],
      "env": {
        "CHUNKHOUND_MEMORY_DIR": "/absolute/path/to/memory"
      }
    }
  }
}
```

If your client expects a full path to `uv` or `chunkhound`, substitute accordingly (`which uv` / `where.exe uv`).

---

## 4. Connect specific harnesses

UI labels change over time; if a menu name differs, search the product settings for **MCP** or **Integrations**. The JSON below is what matters.

### Claude Code

**HTTP (recommended):**

```bash
claude mcp add --transport http -s user chunkhound-memory \
  http://MEMORY_HOST:8765/mcp \
  --header "Authorization: Bearer YOUR_TOKEN"
```

**Stdio (single machine, no serve):**

```bash
claude mcp add -s user chunkhound-memory -- \
  uv run chunkhound memory mcp
```

Then set `CHUNKHOUND_MEMORY_DIR` in the environment Claude Code inherits, or use:

```bash
claude mcp add -s user chunkhound-memory --env CHUNKHOUND_MEMORY_DIR=/data/chunkhound-memory -- \
  uv run chunkhound memory mcp
```

**Project-local file:** some setups also honor `.mcp.json` in the project root with the generic JSON from section 3.

Verify:

```bash
claude mcp list
```

You should see `chunkhound-memory` connected and tools such as `memory_research`, `memory_store`.

### Claude Desktop / Claude Cowork (and similar “remote connector” UIs)

1. Open **Settings → Integrations / MCP / Connectors** (wording varies by app version).
2. **Add custom MCP** / **Add connector**.
3. Prefer **URL / remote / HTTP** transport:
   - Name: `chunkhound-memory`
   - URL: `http://MEMORY_HOST:8765/mcp`
   - Header: `Authorization` = `Bearer YOUR_TOKEN`  
     (or `X-ChunkHound-Token` = `YOUR_TOKEN` if the UI only allows custom header names)
4. Save and restart the app if tools do not appear.

**Claude Desktop config file** (when using file-based MCP config; paths vary by OS):

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "url": "http://MEMORY_HOST:8765/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

If Desktop only supports stdio on your build, use the stdio block from section 3B **on the same machine as the memory dir**, or use a local proxy that speaks stdio → remote HTTP (only if you already use such a bridge). Prefer native HTTP when available.

Cowork and other Anthropic-adjacent clients that accept **remote MCP URLs** use the same URL + bearer pattern as above.

### Grok Build

1. Open Grok Build MCP / tools settings for the session or user.
2. Add an MCP server entry using **HTTP / remote** form:

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "url": "http://MEMORY_HOST:8765/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

3. Restart the session or reload MCP servers.
4. Confirm tools `memory_research`, `memory_store`, `memory_archive` are listed.

If Grok Build only offers a “command” form on your build, use stdio (section 3B) only when this machine owns the memory directory and **serve is not running**.

### Cursor

1. **Settings → MCP** (or **Tools & Integrations → MCP**), or edit:
   - Project: `.cursor/mcp.json`
   - Global: `~/.cursor/mcp.json`
2. Add:

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "url": "http://MEMORY_HOST:8765/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Some Cursor builds accept:

```json
{
  "mcpServers": {
    "chunkhound-memory": {
      "type": "http",
      "url": "http://MEMORY_HOST:8765/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

3. Enable the server and refresh. Green/connected status should list the memory tools.

### VS Code (GitHub Copilot Chat / MCP-enabled builds)

1. Open MCP settings (Command Palette → “MCP” / “Configure MCP servers”), or edit the MCP config JSON used by your VS Code MCP extension.
2. Add the same **url + headers** block as Cursor (section above).
3. Reload the window if tools do not appear.

Exact key names (`mcpServers` vs `servers`) depend on the VS Code MCP extension version; always prefer the schema your Command Palette template inserts, then fill in `url` and `Authorization`.

### Windsurf, Zed, and other MCP clients

Use the **generic HTTP pattern** from section 3A:

- Server name: `chunkhound-memory`
- Transport: Streamable HTTP / HTTP / remote URL
- URL: `http://MEMORY_HOST:8765/mcp`
- Header: `Authorization: Bearer YOUR_TOKEN`

If the client only documents stdio, use 3B and accept single-process limits.

---

## 5. Smoke test after connecting

From any machine that can reach the host:

```bash
# Health (no token)
curl -s http://MEMORY_HOST:8765/health
# Expect: {"status":"ok","service":"chunkhound-memory"}
```

In the harness:

1. Ask the agent to call **memory_research** with a query like  
   `user preferences and skills for how I like plans and tests`.
2. After a durable decision, ask it to **memory_store** a short `decision` (or approve a draft **skill**).
3. From a **second** harness or machine, **memory_research** the same topic and confirm the entry appears.

If research fails with “embedding provider”, fix `.chunkhound.json` on the host and restart serve / re-index.

---

## 6. Environment variables (host)

| Variable | Meaning |
|----------|---------|
| `CHUNKHOUND_MEMORY_DIR` | Memory directory path |
| `CHUNKHOUND_MEMORY_TOKEN` | Shared bearer token for HTTP clients |
| `CHUNKHOUND_MEMORY_HOST` | Default bind host for serve |
| `CHUNKHOUND_MEMORY_PORT` | Default bind port (default `8765`) |

---

## 7. Security notes (LAN / corporate)

- Memory is intended for a **trusted internal network** and a small set of users (often just you).
- The token is a shared secret, not multi-user OAuth. Anyone with the token can read and write memory.
- Prefer binding to LAN only; do not expose the port to the public internet without a reverse proxy, TLS, and a stronger access model.
- Plain HTTP is acceptable on isolated corporate LAN; use TLS (reverse proxy) if traffic crosses untrusted segments.
- Do not store secrets (API keys, passwords) inside memory entries.

---

## 8. Troubleshooting

| Symptom | Check |
|---------|--------|
| Client cannot connect | Firewall on host; `MEMORY_HOST` IP; serve process still running; URL path ends with `/mcp` |
| `401 Unauthorized` | Token mismatch; try both `Authorization: Bearer` and `X-ChunkHound-Token` |
| Tools missing embeddings | Configure `embedding` in memory `.chunkhound.json`; restart serve; re-index |
| “Memory directory already owned” | Another `memory serve` or `memory mcp` holds the lock; stop the other process |
| DuckDB lock errors | Same root cause — two processes on one DB; use one serve + HTTP clients only |
| Second machine does not see new memories | Confirm both clients use the **same** serve URL; store returned `indexed: true` |
| Windows vs Linux clients | HTTP is OS-agnostic; only the **host** needs ChunkHound installed |

Lock file (do not delete while serve is healthy):  
`<memory-dir>/.chunkhound/memory-owner.lock`

---

## 9. Related code & policy

| Path | Purpose |
|------|---------|
| `chunkhound/services/memory/agent_protocol.py` | Canonical agent usage policy (server instructions + tool summary) |
| `chunkhound/mcp_server/memory_*.py` | Memory MCP server & tools |
| `chunkhound/api/cli/commands/memory_*.py` | `init` / `mcp` / `serve` CLI |
| `docs/memory-setup.md` | This setup guide |

CLI quick reference:

```bash
chunkhound memory init [--dir PATH]
chunkhound memory serve --dir PATH --host 0.0.0.0 --port 8765 --token SECRET
chunkhound memory mcp [--dir PATH]    # stdio; single owner only
```

After connect, you should not need to paste usage instructions into each harness: the MCP server already instructs the model how to start sessions with `memory_research`, load skills mid-task, require approval for new skills, and archive or supersede bad memories.
