"""Post-search metadata filtering for Memory MCP tools."""

from __future__ import annotations

import re
from typing import Any

_TYPE_HINT_RE = re.compile(
    r"\b(user_preference|preference|skill|lesson|failure|decision)\b",
    re.IGNORECASE,
)
_PROJECT_HINT_RE = re.compile(r"\bproject:([^\s,;]+)", re.IGNORECASE)
_TAG_HINT_RE = re.compile(r"\btag:([^\s,;]+)", re.IGNORECASE)


def filter_chunks_by_task_context(
    chunks: list[dict[str, Any]],
    task_context: str,
) -> list[dict[str, Any]]:
    """Boost chunks whose frontmatter matches task_context hints.

    Supports:
    - type words: preference, skill, lesson, failure, decision
    - project:<name>
    - tag:<name>
    """
    if not task_context.strip():
        return chunks

    hinted_types = _types_from_context(task_context)
    projects = {m.group(1).lower() for m in _PROJECT_HINT_RE.finditer(task_context)}
    tags = {m.group(1).lower() for m in _TAG_HINT_RE.finditer(task_context)}

    if not hinted_types and not projects and not tags:
        return chunks

    def score(chunk: dict[str, Any]) -> int:
        points = 0
        meta_type = _chunk_type(chunk)
        if hinted_types and meta_type in hinted_types:
            points += 3
        meta = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        project = str(meta.get("project") or "").lower()
        if projects and project and project in projects:
            points += 2
        chunk_tags = meta.get("tags") or []
        if isinstance(chunk_tags, list):
            lowered = {str(t).lower() for t in chunk_tags}
            if tags and lowered.intersection(tags):
                points += 1
        return points

    ranked = sorted(chunks, key=score, reverse=True)
    # If nothing scored, keep original order
    if all(score(c) == 0 for c in ranked):
        return chunks
    return ranked


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
