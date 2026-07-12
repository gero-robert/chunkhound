# Memory setup scripts

Companion automation for [../memory-setup.md](../memory-setup.md).

| Script | Purpose |
|--------|---------|
| `setup.sh` / `setup.ps1` | Interactive: init dir, embeddings, index, token, write `memory-serve.env`, print client configs |
| `serve.sh` / `serve.ps1` | Start `chunkhound memory serve` (loads `memory-serve.env`) |
| `client-config.sh` / `client-config.ps1` | Print MCP JSON / Claude Code command for harnesses |
| `env.example` | Template for `memory-serve.env` |

## Quick start

**Windows (host):**

```powershell
cd <repo>
.\docs\memory\setup.ps1
# later / reboot:
.\docs\memory\serve.ps1
```

**Linux / macOS (host):**

```bash
cd <repo>
chmod +x docs/memory/*.sh
./docs/memory/setup.sh
# later / reboot:
./docs/memory/serve.sh
```

Scripts always `uv run` from the **repository root** (parent of `docs/`). Run them from a full clone of this branch.

## What is automated vs manual

| Step | Automated? |
|------|------------|
| `memory init` | Yes |
| Embedding provider + API key into `.chunkhound.json` | Yes (prompted) |
| Re-index | Yes (optional prompt) |
| Token + bind host/port + `memory-serve.env` | Yes |
| Print client snippets | Yes |
| Start serve | Optional prompt / `serve.*` |
| Firewall open | **Manual** (Windows command printed) |
| Paste config into Claude / Cursor / Grok / etc. | **Manual** (snippets printed) |
| LLM provider for research summaries | **Manual** (edit `.chunkhound.json`) |
| systemd / Task Scheduler for auto-start | **Manual** |

Agent *usage* policy (when to research/store/archive) is inside the MCP — not configured by these scripts.
