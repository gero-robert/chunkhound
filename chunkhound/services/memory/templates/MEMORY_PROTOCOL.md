# ChunkHound Memory Protocol

This directory is your agent's long-term memory. ChunkHound indexes these files so MCP tools can retrieve preferences, skills, and lessons.

## Mandatory workflow

ALWAYS start by calling memory_research with a query derived from the current user request plus any known user preferences.
If you discover new information about how the user wants you to behave, or what worked/didn't work, append a new entry using the exact Markdown + YAML frontmatter format shown in the templates.
You have two memory tools: prefer memory_research first (it gives summarized insight); use memory_semantic_search only when you need raw entries.
Document which tool gave better results in your next learning entry so we can evolve the system.

## Reading memory

1. Call `memory_research(query, task_context="")` at the start of every task.
2. Use `memory_semantic_search(query)` only when you need verbatim raw entries.

## Writing memory

Append or create `.md` files under:

- `preferences/` for `type: user_preference`
- `skills/` for `type: skill`
- `lessons/` for `type: lesson` or `type: failure`

The realtime watcher re-indexes file changes automatically. No write tool is provided in v1.

## Entry format

```markdown
---
type: user_preference
applies_to: planning
learned_at: 2026-07-03
tags: [concise, bullets]
confidence: high
---

## Title

Body text.
```

See the template files in this directory for examples.