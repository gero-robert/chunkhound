"""Post-search metadata filtering for Memory MCP tools."""

from __future__ import annotations

import re
from typing import Any

_TYPE_HINT_RE = re.compile(
    r"\b(user_preference|preference|skill|lesson|failure)\b", re.IGNORECASE
)


def filter_chunks_by_task_context(
    chunks: list[dict[str, Any]],
    task_context: str,
) -> list[dict[str, Any]]:
    """Boost chunks whose frontmatter type matches task_context hints."""
    if not task_context.strip():
        return chunks

    hinted_types = _types_from_context(task_context)
    if not hinted_types:
        return chunks

    matched = [
        chunk
        for chunk in chunks
        if _chunk_type(chunk) in hinted_types
    ]
    if not matched:
        return chunks

    unmatched = [chunk for chunk in chunks if chunk not in matched]
    return matched + unmatched


def _types_from_context(task_context: str) -> set[str]:
    types: set[str] = set()
    for match in _TYPE_HINT_RE.findall(task_context):
        lowered = match.lower()
        if lowered == "preference":
            types.add("user_preference")
        else:
            types.add(lowered)
    return types


def _chunk_type(chunk: dict[str, Any]) -> str | None:
    metadata = chunk.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("type")
    return value if isinstance(value, str) else None