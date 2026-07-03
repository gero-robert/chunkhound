"""Contract tests for prose chunker used by Memory MCP indexing."""

from __future__ import annotations

from pathlib import Path

import pytest

from chunkhound.core.types.common import FileId
from chunkhound.parsers.parser_factory import create_parser_for_file
from chunkhound.parsers.prose import ProseParser, is_prose_file


SAMPLE_MEMORY_MD = """\
---
type: user_preference
applies_to: planning
learned_at: 2026-07-03
tags: [concise]
confidence: high
---

# User Preferences

The user prefers concise answers with minimal filler.

## Planning Style

Always use bullet points for plans. Never use numbered lists for task breakdowns.

## Code Examples

```python
def example():
    return "keep fenced blocks intact"
```

- Prefer small diffs
- Avoid drive-by refactors
- Run smoke tests before commit

This sentence ends here. This is a second sentence in the same paragraph that should stay together when the paragraph fits within chunk limits.
"""


@pytest.fixture
def memory_file(tmp_path: Path) -> Path:
    path = tmp_path / "preferences.md"
    path.write_text(SAMPLE_MEMORY_MD, encoding="utf-8")
    return path


def test_is_prose_file_recognizes_memory_extensions() -> None:
    assert is_prose_file(Path("notes.md"))
    assert is_prose_file(Path("readme.txt"))
    assert not is_prose_file(Path("main.py"))


def test_prose_chunker_preserves_heading_context(memory_file: Path) -> None:
    parser = ProseParser()
    chunks = parser.parse_file(memory_file, FileId(1))
    contents = "\n".join(chunk.code for chunk in chunks)

    assert any("Planning Style" in chunk.code for chunk in chunks)
    assert "bullet points" in contents
    assert any("User Preferences" in contents for chunk in chunks)


def test_prose_chunker_preserves_fenced_code_block(memory_file: Path) -> None:
    parser = ProseParser()
    chunks = parser.parse_file(memory_file, FileId(1))

    code_chunks = [c for c in chunks if "def example" in c.code]
    assert code_chunks, "expected fenced code block chunk"
    assert "```python" in code_chunks[0].code
    assert 'return "keep fenced blocks intact"' in code_chunks[0].code


def test_prose_chunker_does_not_split_mid_sentence(memory_file: Path) -> None:
    parser = ProseParser()
    chunks = parser.parse_file(memory_file, FileId(1))

    paragraph_chunks = [
        c
        for c in chunks
        if "This sentence ends here" in c.code or "second sentence" in c.code
    ]
    assert paragraph_chunks
    combined = " ".join(c.code for c in paragraph_chunks)
    assert "This sentence ends here." in combined
    assert "second sentence" in combined
    for chunk in paragraph_chunks:
        text = chunk.code.strip()
        assert not text.endswith("here. This is"), "paragraph split mid-sentence"


def test_parser_factory_uses_prose_chunker_when_configured(memory_file: Path) -> None:
    prose_parser = create_parser_for_file(memory_file, chunker="prose")
    assert isinstance(prose_parser, ProseParser)

    cast_parser = create_parser_for_file(memory_file, chunker="cast")
    assert not isinstance(cast_parser, ProseParser)


def test_prose_chunker_strips_frontmatter_from_chunk_content(memory_file: Path) -> None:
    parser = ProseParser()
    chunks = parser.parse_file(memory_file, FileId(1))
    all_content = "\n".join(chunk.code for chunk in chunks)

    assert "type: user_preference" not in all_content
    assert "User Preferences" in all_content


def test_prose_chunker_falls_back_to_cast_for_code_files(tmp_path: Path) -> None:
    code_file = tmp_path / "app.py"
    code_file.write_text("def main():\n    pass\n", encoding="utf-8")

    parser = create_parser_for_file(code_file, chunker="prose")
    assert not isinstance(parser, ProseParser)


def test_batch_processor_uses_prose_chunker_when_configured(
    memory_file: Path,
) -> None:
    from chunkhound.services.batch_processor import process_file_batch

    results = process_file_batch(
        [memory_file],
        {
            "chunker": "prose",
            "per_file_timeout_seconds": 0,
            "detect_embedded_sql": False,
            "index_unknown_files": False,
        },
    )

    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].chunks
    assert any("bullet points" in chunk.get("code", "") for chunk in results[0].chunks)