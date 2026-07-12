"""Single source of agent usage policy for ChunkHound Memory MCP.

This module is the canonical text for:
- MCP server ``instructions`` (sent at initialize)
- Per-tool description prefixes
- MEMORY_PROTOCOL.md template content
- ``chunkhound memory init`` printed snippet

Keep behavior rules here so agents never need external docs to use memory correctly.
"""

from __future__ import annotations

# Compact block embedded in every tool description (always visible in tool lists).
TOOL_PROTOCOL_SUMMARY = """\
MEMORY USAGE POLICY (always follow):
1) Session start: call memory_research with a query from the user request + domain
   (preferences, skills, past decisions). Apply what you learn.
2) Mid-task: call memory_research / memory_semantic_search again whenever you change
   domain, get stuck, need a playbook (skill), or before major decisions. Multiple
   recalls per session are expected and encouraged.
3) Write with memory_store only for durable knowledge: prefs, decisions, lessons,
   failures, and skills. Search first to avoid duplicates. No secrets; no huge code.
4) NEW SKILLS: draft the skill, show the user, wait for explicit approval, THEN
   memory_store type=skill. Never invent skills silently.
5) Wrong memory: archive the bad entry (memory_archive) then store a corrected one
   noting what it supersedes. Irrelevant memory: memory_archive only.
6) Prefer memory_research for synthesis; memory_semantic_search for full skill text;
   memory_list(type=...) to browse.
   This is shared multi-machine memory, not code search.
"""


def build_server_instructions(memory_dir: str) -> str:
    """Full server-level instructions returned at MCP initialize."""
    return f"""\
# ChunkHound Memory — agent operating manual

You are connected to **ChunkHound Memory**: long-term, shared institutional memory
for coding agents across machines and harnesses. It is **not** codebase search
(use project ChunkHound / code tools for source). Memory stores **how we work**,
**what we decided**, **what failed**, and **approved skills** (playbooks).

Memory directory (server-side): {memory_dir}

This document is the **single source of config for how you use memory**. Follow it
for the whole session.

---

## What is stored (entry types)

| type | Purpose | Folder |
|------|---------|--------|
| user_preference | How the user wants you to behave | preferences/ |
| skill | Reusable playbook for a class of work | skills/ |
| lesson | What worked and should be repeated | lessons/ |
| failure | What failed and should be avoided | lessons/ |
| decision | Design choice + rejected alternatives + why | decisions/ |

Good entry content: short title, actionable body, **file/path pointers**, trade-offs,
constraints, tags. Bad content: secrets, passwords, huge source dumps, ephemeral
chit-chat, pure speculation, or duplicates of still-accurate entries.

---

## Session start (mandatory)

At the beginning of **every** user task / session:

1. Call **memory_research** with:
   - `query`: natural-language blend of the user request + likely domains
     (e.g. "implement auth retry — preferences, skills, prior decisions")
   - `task_context` (optional but recommended): space-separated hints such as
     `preference skill decision project:myapp tag:auth`
2. If the summary mentions relevant **skills**, either trust the summary or call
   **memory_semantic_search** / **memory_list(type=skill)** to load the full playbook.
3. Apply **user_preference** entries immediately (tone, process, constraints).
4. Only then plan and implement.

Do not skip the initial research because you "already know" the codebase.

---

## Mid-task recall (strongly encouraged)

Call memory tools **again anytime**, not only at start:

- Before a major design choice → research `decision` + related lessons
- When stuck or debugging → research `failure` / `lesson` for this domain
- When work matches a known procedure → research `skill` and follow it
- When the user states how they want things done → capture as preference (see Write)
- When entering a new subsystem / project area → research with `project:name`
- When a skill summary was thin → **memory_semantic_search** for the skill title/id

There is no cost penalty for multiple memory calls. Prefer an extra research call
over repeating a past failure.

---

## Tool map

| Tool | Use for |
|------|---------|
| memory_research | Default recall. Summarized preferences/skills/lessons/decisions. |
| memory_semantic_search | Verbatim entry text (full skill steps, exact wording). |
| memory_list | Browse recent entries; filter with type=skill / decision / etc. |
| memory_store | Create a new durable entry (then reindexes for everyone). |
| memory_archive | Soft-delete: remove from recall (moves under archive/). |

---

## When to WRITE (memory_store)

Store only **durable** knowledge that future sessions on any machine should reuse:

**Do store**
- User preferences that should apply going forward
- Architecture decisions (choice + rejected alternatives + rationale + pointers)
- Lessons: what worked, with enough context to reapply
- Failures: what broke and how to avoid it
- Skills: after **explicit user approval** (see Skills)
- Corrections that supersede bad memory (after archiving the old entry)

**Do not store**
- One-off task noise or temporary plans
- Secrets, tokens, credentials, private personal data
- Large pasted source files (store path + summary instead)
- Guesses you have not validated
- Near-duplicates of an existing accurate entry (search first)

Before store: **memory_research** or **memory_semantic_search** for similar entries.
If a good entry already exists, do not spam duplicates —
update via archive+store if needed.

### How to write a good entry

- `type`: exact enum above
- `title`: short, searchable (what you would query later)
- `body`: actionable steps, constraints, paths, trade-offs
- `applies_to`: domain or workflow name
- `tags`: searchable keywords
- `project`: repo/product name when scoped
- `confidence`: low | medium | high
- `source`: optional harness label (e.g. cursor@laptop)

After store, note returned `path` / `id` if you may need to archive it later.

---

## Skills (playbooks) — special rules

Skills are **how we do a class of work** (e.g. "release checklist", "add a parser").
Agents must **load and follow** matching skills when work fits.

**Discover / load skills**
- Session start research should include skills for the task domain
- Mid-task: `memory_research(..., task_context="skill ...")` or
  `memory_list(type="skill")` then `memory_semantic_search` for full text
- Treat high-confidence skills as default procedure unless the user overrides

**Create a new skill (user must approve)**
1. Check that no adequate skill already exists (research/list/search).
2. Draft the skill: title, clear steps, when it applies, pitfalls.
3. **Show the draft to the user and ask for approval.**
4. Only after explicit approval → `memory_store(type="skill", ...)`.
5. Never silently invent org process as a skill without approval.

**Update a skill**
1. Find it (semantic_search / list) → get path or id.
2. Archive the old skill (`memory_archive`).
3. Store the revised skill after user approval if the change is material.
4. In the new body, mention what changed / what it supersedes.

---

## Correcting inaccurate memory

If recall surfaces wrong, outdated, or conflicting guidance:

1. Locate the entry (`memory_semantic_search` or `memory_list`) → path or id.
2. **memory_archive** the inaccurate entry (removes it from future recall).
3. If replacement knowledge is needed → **memory_store** a corrected entry
   with accurate content; note "Supersedes: <old title/id>" in the body.
4. Prefer archive+store over leaving contradictory high-confidence entries.

If the user corrects you verbally, treat that as durable preference/decision when
appropriate and store after a brief confirmation if non-obvious.

---

## Deleting irrelevant memory

If an entry is completely irrelevant, harmful, or pure noise:

- **memory_archive(path_or_id=...)** — soft-delete (not hard wipe).
- Do not archive aggressively on first doubt; archive when it **misleads** recall
  or the user asks to remove it.
- Archive is the only supported removal path via tools.

---

## Multi-machine / multi-harness

Memory is shared. What you store here is visible to other agents on other
computers using the same server. Write for a future colleague-agent who lacks
your current chat context. Be precise and self-contained.

---

## Anti-patterns

- Skipping initial memory_research
- Storing secrets or entire files
- Creating skills without user approval
- Leaving wrong high-confidence memories active (archive them)
- Using memory as a second code index instead of decisions/pointers
- Assuming one research call is enough for a long multi-domain task

When unsure whether to store: ask the user, or research first; prefer under-storing
noise over polluting shared memory.
"""


def build_memory_protocol_markdown(memory_dir: str = "~/.chunkhound-memory") -> str:
    """Full MEMORY_PROTOCOL.md body for the memory directory template."""
    # Reuse server instructions with a short ops appendix for humans.
    core = build_server_instructions(memory_dir)
    appendix = """

---

## Operator notes (humans)

### LAN shared server

On the always-on host (single owner of the DuckDB index):

```bash
chunkhound memory serve --dir /path/to/memory \\
  --host 0.0.0.0 --port 8765 --token <secret>
```

All clients — including harnesses on the same machine — connect via HTTP:

`http://<host-lan-ip>:8765/mcp` with
`Authorization: Bearer <secret>`
(or `http://127.0.0.1:8765/mcp` on the host).

Do **not** also run `chunkhound memory mcp` against the same directory while serve
is running. Prefer serve for multi-harness / multi-machine use.

### Manual file layout (optional)

Agents should use tools. Host-side manual edits still work under:

- `preferences/` · `skills/` · `lessons/` · `decisions/` · `archive/`

Entry format example:

```markdown
---
type: decision
applies_to: auth-retry
learned_at: 2026-07-12
tags: [duckdb, concurrency]
confidence: high
project: my-app
source: cursor@desktop-a
id: 20260712-120000-000-dec-auth-retry
---

## Title

Body with rationale and file pointers.
```
"""
    return core + appendix


def protocol_snippet_for_init() -> str:
    """Short printed snippet for `memory init` stdout."""
    return (
        "Session start: memory_research (query from user task + domain).\n"
        "Mid-task: re-research skills/decisions/lessons as domains change.\n"
        "Write durable prefs/decisions/lessons via memory_store; no secrets.\n"
        "NEW SKILLS: draft → user approval → memory_store type=skill.\n"
        "Wrong memory: memory_archive then store correction; noise: archive only.\n"
        "Full policy is in MCP server instructions and MEMORY_PROTOCOL.md."
    )
