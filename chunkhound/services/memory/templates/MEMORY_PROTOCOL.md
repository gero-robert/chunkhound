# ChunkHound Memory Protocol

This directory is your agents' long-term **shared** memory. ChunkHound indexes these files so MCP tools can retrieve preferences, skills, lessons, and decisions across machines and harnesses.

## Mandatory workflow

ALWAYS start by calling memory_research with a query derived from the current user request plus any known user preferences.
After important decisions, patterns, failures, or durable prefs, call memory_store so other machines/sessions share the knowledge.
Prefer memory_research first (summarized insight); use memory_semantic_search only when you need raw entries.
Use memory_archive for obsolete entries that mislead recall.
Store pointers and trade-offs, not large source dumps.

## Reading memory

1. Call `memory_research(query, task_context="")` at the start of every task.
2. Use `memory_semantic_search(query)` only when you need verbatim raw entries.
3. Optional `task_context` hints: type words (`preference`, `skill`, `lesson`, `failure`, `decision`), `project:name`, `tag:name`.

## Writing memory

**Preferred (shared LAN server):** call `memory_store` with type, title, and body.

**Manual (host only):** append or create `.md` files under:

- `preferences/` for `type: user_preference`
- `skills/` for `type: skill`
- `lessons/` for `type: lesson` or `type: failure`
- `decisions/` for `type: decision`

Archived entries live under `archive/` and are excluded from indexing.

## Entry format

```markdown
---
type: decision
applies_to: auth-retry
learned_at: 2026-07-12
tags: [duckdb, concurrency]
confidence: high
project: my-app
source: cursor@desktop-a
id: 20260712-120000-000-auth-retry
---

## Title

Body text with rationale and file pointers.
```

## LAN shared server

On the always-on host:

```bash
chunkhound memory serve --dir /path/to/memory --host 0.0.0.0 --port 8765 --token <secret>
```

Clients on any machine point their MCP config at `http://<host-lan-ip>:8765/mcp` with `Authorization: Bearer <secret>`.
