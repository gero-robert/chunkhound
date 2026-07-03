"""Self-contained MCP tool descriptions for the Memory server."""

from __future__ import annotations

_PROTOCOL = (
    "ALWAYS start by calling memory_research with a query derived from the current "
    "user request plus any known user preferences.\n"
    "If you discover new information about how the user wants you to behave, or "
    "what worked/didn't work, append a new entry using the exact Markdown + YAML "
    "frontmatter format shown in the templates.\n"
    "You have two memory tools: prefer memory_research first (it gives summarized "
    "insight); use memory_semantic_search only when you need raw entries.\n"
    "Document which tool gave better results in your next learning entry so we can "
    "evolve the system."
)


def build_memory_research_description(memory_dir: str) -> str:
    """Build the memory_research tool description."""
    return f"""ChunkHound Memory — global long-term agent memory (NOT codebase search).

MEMORY DIRECTORY: {memory_dir}
Configure via CHUNKHOUND_MEMORY_DIR or `chunkhound memory init --dir PATH`.

START HERE for every task. Returns a concise markdown summary of relevant preferences, skills, and lessons.

MANDATORY WORKFLOW:
{_PROTOCOL}

WHEN TO USE:
- Beginning of every user task
- Before making plans or writing code
- When you need synthesized insight across many memory entries

ARGS:
- query (required): Combine the user request with known preferences, e.g. "refactor auth — user style and past lessons"
- task_context (optional): Scope hints, e.g. "type:preference, domain:planning"

WRITING NEW MEMORY:
Edit files directly under {memory_dir}:
- preferences/ for type: user_preference
- skills/ for type: skill
- lessons/ for type: lesson or failure
Use the YAML frontmatter template from MEMORY_PROTOCOL.md. The watcher re-indexes automatically.

ENTRY TYPES:
- user_preference — how the user wants you to behave
- skill — how we do things here
- lesson — what worked
- failure — what did not work

DEGRADED MODE: Without a reranker, results use top semantic hits with a shorter summary.
"""


def build_memory_semantic_search_description(memory_dir: str) -> str:
    """Build the memory_semantic_search tool description."""
    return f"""ChunkHound Memory — raw semantic hits from the global memory index.

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

RETURNS: Markdown blocks with file path, line range, frontmatter metadata, and full chunk text.

WRITING NEW MEMORY: Append .md files under {memory_dir} (preferences/, skills/, lessons/) using the template format in MEMORY_PROTOCOL.md.
"""