"""Self-contained MCP tool descriptions for the Memory server."""

from __future__ import annotations

_PROTOCOL = (
    "ALWAYS start by calling memory_research with a query derived from the current "
    "user request plus any known user preferences.\n"
    "After important decisions, patterns, failures, or durable prefs, call "
    "memory_store so other machines/sessions share the knowledge.\n"
    "Prefer memory_research first (summarized insight); use memory_semantic_search "
    "only when you need raw entries.\n"
    "Use memory_archive for obsolete entries that mislead recall.\n"
    "Store pointers and trade-offs, not large source dumps."
)


def build_memory_research_description(memory_dir: str) -> str:
    """Build the memory_research tool description."""
    return f"""ChunkHound Memory — shared long-term agent memory (NOT codebase search).

MEMORY DIRECTORY: {memory_dir}
Configure via CHUNKHOUND_MEMORY_DIR or `chunkhound memory init --dir PATH`.
On LAN, connect via `chunkhound memory serve` URL instead of local files.

START HERE for every task. Returns a concise markdown summary of relevant
preferences, skills, lessons, and decisions.

MANDATORY WORKFLOW:
{_PROTOCOL}

WHEN TO USE:
- Beginning of every user task
- Before making plans or writing code
- When you need synthesized insight across many memory entries

ARGS:
- query (required): Combine the user request with known preferences
- task_context (optional): Hints such as type words
  (preference, skill, lesson, failure, decision), project:name, tag:name

WRITING NEW MEMORY:
Call memory_store (preferred on shared server).
Types: user_preference, skill, lesson, failure, decision.

ENTRY TYPES:
- user_preference — how the user wants you to behave
- skill — how we do things here
- lesson — what worked
- failure — what did not work
- decision — architecture trade-offs and choices
"""


def build_memory_semantic_search_description(memory_dir: str) -> str:
    """Build the memory_semantic_search tool description."""
    return f"""ChunkHound Memory — raw semantic hits from the shared memory index.

MEMORY DIRECTORY: {memory_dir}

Use only when memory_research lacks detail or you need verbatim entries.

MANDATORY WORKFLOW:
{_PROTOCOL}

WHEN TO USE:
- memory_research summary is too vague
- You need exact wording from a stored entry
- Debugging which memory entry matched

ARGS:
- query (required): Natural language query against memory chunks

RETURNS: Markdown blocks with file path, line range, frontmatter metadata,
and full chunk text.
"""


def build_memory_store_description(memory_dir: str) -> str:
    """Build the memory_store tool description."""
    return f"""Create a durable memory entry on the shared server
(writes Markdown + reindexes).

MEMORY DIRECTORY: {memory_dir}

Use after important work so other harnesses/machines can recall it.

ARGS:
- type (required): user_preference | skill | lesson | failure | decision
- title (required): Short heading
- body (required): Actionable content — decisions, pointers, trade-offs
  (not huge code dumps)
- applies_to (optional): Scope string
- tags (optional): list of tags
- confidence (optional): low | medium | high (default medium)
- project (optional): project name for filtering
- source (optional): harness/machine label

RETURNS: path, id, indexed status.
"""


def build_memory_archive_description(memory_dir: str) -> str:
    """Build the memory_archive tool description."""
    return f"""Soft-delete a memory entry by moving it to archive/ under {memory_dir}.

Use when an entry is obsolete or misleading. Prefer archive over rewriting history.

ARGS:
- path_or_id (required): Relative path (e.g. lessons/foo.md) or entry id
"""


def build_memory_list_description(memory_dir: str) -> str:
    """Build the memory_list tool description."""
    return f"""List recent memory entries under {memory_dir} (filesystem, not semantic).

Use for debugging or browsing. Prefer memory_research for task-relevant recall.

ARGS:
- type (optional): filter by entry type
- limit (optional): max entries (default 20, max 100)
"""
