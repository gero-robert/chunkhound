"""Contract tests for memory frontmatter extraction and filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from chunkhound.core.types.common import FileId
from chunkhound.parsers.frontmatter import FrontmatterExtractor
from chunkhound.parsers.prose import ProseParser
from chunkhound.services.memory.metadata_filter import filter_chunks_by_task_context


SAMPLE_WITH_FRONTMATTER = """\
---
type: user_preference
applies_to: planning
learned_at: 2026-07-03
tags: [concise, bullets]
confidence: high
---

# Preferences

Always use bullet points for plans.
"""


def test_frontmatter_extractor_parses_memory_schema() -> None:
    extractor = FrontmatterExtractor()
    body, fields = extractor.extract(SAMPLE_WITH_FRONTMATTER)

    assert "Preferences" in body
    assert fields["type"] == "user_preference"
    assert fields["applies_to"] == "planning"
    assert fields["learned_at"] == "2026-07-03"
    assert fields["tags"] == ["concise", "bullets"]
    assert fields["confidence"] == "high"


def test_prose_parser_injects_frontmatter_into_chunk_metadata(tmp_path: Path) -> None:
    path = tmp_path / "preference.md"
    path.write_text(SAMPLE_WITH_FRONTMATTER, encoding="utf-8")

    chunks = ProseParser().parse_file(path, FileId(1))
    assert chunks
    assert all(chunk.metadata and chunk.metadata.get("type") == "user_preference" for chunk in chunks)
    assert all(chunk.metadata.get("applies_to") == "planning" for chunk in chunks)


def test_metadata_filter_boosts_matching_type() -> None:
    chunks = [
        {"metadata": {"type": "lesson"}, "content": "lesson"},
        {"metadata": {"type": "user_preference"}, "content": "preference"},
    ]

    filtered = filter_chunks_by_task_context(chunks, "preference planning")
    assert filtered[0]["metadata"]["type"] == "user_preference"
    assert filtered[1]["metadata"]["type"] == "lesson"