"""Self-contained MCP tool descriptions for the Memory server.

Each description includes the shared usage policy so a model that only reads
tool schemas still knows when/how to use memory. Full detail is also in
server ``instructions`` at initialize (see agent_protocol).
"""

from __future__ import annotations

from chunkhound.services.memory.agent_protocol import TOOL_PROTOCOL_SUMMARY


def build_memory_research_description(memory_dir: str) -> str:
    """Build the memory_research tool description."""
    return f"""ChunkHound Memory — DEFAULT RECALL tool (shared long-term agent memory).

NOT codebase search. Memory directory: {memory_dir}

{TOOL_PROTOCOL_SUMMARY}

## This tool
Synthesizes relevant preferences, skills, lessons, failures, and decisions into
a concise actionable summary. **Call this first on every task**, and again
mid-task when the domain shifts, you need a skill playbook, or before big
decisions.

## When to call
- Session / task start (mandatory)
- Before planning or architecture choices
- When stuck; when entering a new area; when a skill might apply
- After the user states a durable preference (to see if one already exists)

## Args
- query (required): User request + domain, e.g.
  "add retry to auth client — prefs, skills, decisions"
- task_context (optional): hints like
  `preference skill decision project:myapp tag:auth`

## Tips
- For full skill steps after a hit, follow with memory_semantic_search.
- To browse skills: memory_list(type="skill").
- To save new knowledge: memory_store (skills need user approval first).
- To remove wrong/noisy entries: memory_archive.
"""


def build_memory_semantic_search_description(memory_dir: str) -> str:
    """Build the memory_semantic_search tool description."""
    return f"""ChunkHound Memory — VERBATIM recall (raw semantic hits).

Memory directory: {memory_dir}

{TOOL_PROTOCOL_SUMMARY}

## This tool
Returns full chunk text + frontmatter metadata. Use when memory_research is too
compressed or you need exact skill/procedure wording.

## When to call
- Loading a full **skill** playbook to follow step-by-step
- Verifying exact preference/decision wording
- Finding path/id of an entry before memory_archive or superseding
- Debugging which entry matched

## Args
- query (required): Natural language query (skill title, decision topic, etc.)

## Tips
Prefer memory_research first for overview; use this for depth. Multiple searches
per session are fine.
"""


def build_memory_store_description(memory_dir: str) -> str:
    """Build the memory_store tool description."""
    return f"""ChunkHound Memory — CREATE a durable shared entry (write + reindex).

Memory directory: {memory_dir}

{TOOL_PROTOCOL_SUMMARY}

## This tool
Writes Markdown with frontmatter and indexes it so all machines/harnesses can
recall it. Use for durable institutional knowledge only.

## When to store
- user_preference: lasting behavior the user wants
- decision: choice + alternatives rejected + why + path pointers
- lesson / failure: what worked or failed, with enough context to reuse
- skill: **ONLY after explicit user approval** of the drafted playbook

## When NOT to store
Secrets; huge code dumps; ephemeral task noise; unvalidated guesses;
duplicates of still-accurate entries (research first).

## Correcting memory
Do not silently overwrite. **memory_archive** the bad entry, then store a
corrected one mentioning what it supersedes. For skills, get user approval on
material changes.

## Args
- type (required): user_preference | skill | lesson | failure | decision
- title (required): Short searchable heading
- body (required): Actionable content — steps, trade-offs, file pointers
- applies_to (optional): Domain / workflow name
- tags (optional): list of tags
- confidence (optional): low | medium | high (default medium)
- project (optional): project/repo name
- source (optional): harness label

## Returns
JSON with path, id, indexed, embeddings_ok — keep path/id if you may archive later.
"""


def build_memory_archive_description(memory_dir: str) -> str:
    """Build the memory_archive tool description."""
    return f"""ChunkHound Memory — SOFT-DELETE (remove from future recall).

Memory directory: {memory_dir}

{TOOL_PROTOCOL_SUMMARY}

## This tool
Moves an entry under archive/ and removes it from the search index. Use when
memory is wrong, obsolete, misleading, or the user asks to remove it.

## When to archive
- Inaccurate or outdated guidance (then store a correction if needed)
- Completely irrelevant / noise that pollutes research results
- Superseding a skill or decision (archive old → store new)
- User explicitly requests deletion of a memory

## When not to archive
- Mild uncertainty — research alternatives first
- Entries that are still partially useful; prefer a corrected store after archive
  only when you have better text

## Args
- path_or_id (required): Relative path (e.g. skills/foo.md) or entry id
  (from memory_semantic_search / memory_list / prior memory_store)

## Note
Soft-delete only; preferred over leaving contradictory high-confidence memories.
"""


def build_memory_list_description(memory_dir: str) -> str:
    """Build the memory_list tool description."""
    return f"""ChunkHound Memory — BROWSE recent entries
(filesystem order, not semantic).

Memory directory: {memory_dir}

{TOOL_PROTOCOL_SUMMARY}

## This tool
Lists recent non-archived entries. Use to discover skills/decisions by type or
to find a path/id for archive. Prefer memory_research for task-relevant recall.

## When to call
- "What skills do we have?" → type=skill
- Find an id/path after research pointed at a title
- Operator-style browsing of recent stores

## Args
- type (optional): user_preference | skill | lesson | failure | decision
- limit (optional): max entries (default 20, max 100)
"""
