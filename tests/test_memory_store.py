"""Contract tests for memory store / archive / list services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chunkhound.parsers.frontmatter import MEMORY_TYPES, FrontmatterExtractor
from chunkhound.services.memory.memory_store_service import (
    MemoryStoreService,
    _is_archived_relative,
    _resolve_memory_file,
)
from chunkhound.services.memory.metadata_filter import filter_chunks_by_task_context


class _FakeCoordinator:
    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.removed: list[str] = []
        self.raise_on_remove = False
        self.next_result: dict[str, Any] = {
            "status": "success",
            "chunks": 1,
            "embeddings_skipped": False,
            "embeddings_generated": 1,
            "embedding_error": None,
        }

    async def process_file(
        self, file_path: Path, skip_embeddings: bool = False
    ) -> dict[str, Any]:
        self.paths.append(file_path)
        return dict(self.next_result)

    async def remove_file(
        self, file_path: str, *, raise_on_error: bool = False
    ) -> int:
        if self.raise_on_remove:
            if raise_on_error:
                raise RuntimeError("delete failed")
            return 0
        self.removed.append(file_path)
        return 3


class _FakeServices:
    def __init__(self) -> None:
        self.indexing_coordinator = _FakeCoordinator()


@pytest.mark.asyncio
async def test_store_writes_markdown_and_indexes(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    (memory_dir / "decisions").mkdir(parents=True)
    services = _FakeServices()
    store = MemoryStoreService(memory_dir)

    result = await store.store(
        services=services,  # type: ignore[arg-type]
        entry_type="decision",
        title="Use single memory server",
        body="One process owns DuckDB; clients use HTTP.",
        applies_to="memory-mcp",
        tags=["lan", "duckdb"],
        confidence="high",
        project="chunkhound",
        source="test",
    )

    assert result.indexed is True
    assert result.embeddings_ok is True
    assert result.type == "decision"
    assert "-dec-" in result.id
    path = memory_dir / result.path
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    body, fields = FrontmatterExtractor().extract(text)
    assert fields["type"] == "decision"
    assert fields["project"] == "chunkhound"
    assert fields["source"] == "test"
    assert fields["id"] == result.id
    assert "DuckDB" in body or "DuckDB" in text
    assert services.indexing_coordinator.paths
    assert services.indexing_coordinator.paths[0] == path


@pytest.mark.asyncio
async def test_store_embeddings_ok_false_on_embedding_error(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    (memory_dir / "lessons").mkdir(parents=True)
    services = _FakeServices()
    services.indexing_coordinator.next_result = {
        "status": "success",
        "chunks": 2,
        "embeddings_skipped": False,
        "embeddings_generated": 0,
        "embedding_error": "provider down",
    }
    store = MemoryStoreService(memory_dir)
    result = await store.store(
        services=services,  # type: ignore[arg-type]
        entry_type="lesson",
        title="Embedding failure path",
        body="File still written when embeddings fail.",
    )
    assert result.indexed is True
    assert result.embeddings_ok is False
    assert result.error == "provider down"


@pytest.mark.asyncio
async def test_store_unique_ids_across_types(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    for sub in ("skills", "lessons"):
        (memory_dir / sub).mkdir(parents=True)
    services = _FakeServices()
    store = MemoryStoreService(memory_dir)

    a = await store.store(
        services=services,  # type: ignore[arg-type]
        entry_type="skill",
        title="Same Title",
        body="skill body",
    )
    b = await store.store(
        services=services,  # type: ignore[arg-type]
        entry_type="lesson",
        title="Same Title",
        body="lesson body",
    )
    assert a.id != b.id
    assert "-skill-" in a.id
    assert "-lesson-" in b.id


@pytest.mark.asyncio
async def test_archive_moves_file_and_removes_index(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    lessons = memory_dir / "lessons"
    lessons.mkdir(parents=True)
    entry = lessons / "old.md"
    entry.write_text(
        "---\ntype: lesson\nid: old-1\n---\n\n## Old\n\nbody\n",
        encoding="utf-8",
    )
    services = _FakeServices()
    store = MemoryStoreService(memory_dir)

    result = await store.archive(
        services=services, path_or_id="lessons/old.md"  # type: ignore[arg-type]
    )
    assert result.path == "lessons/old.md"
    assert result.archived_path.startswith("archive/")
    assert result.chunks_removed == 3
    assert result.indexed is True
    assert result.error is None
    assert not entry.exists()
    assert (memory_dir / result.archived_path).is_file()
    assert services.indexing_coordinator.removed
    assert not services.indexing_coordinator.paths


@pytest.mark.asyncio
async def test_archive_reports_index_failure(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    lessons = memory_dir / "lessons"
    lessons.mkdir(parents=True)
    entry = lessons / "gone.md"
    entry.write_text(
        "---\ntype: lesson\nid: gone-1\n---\n\n## Gone\n\nbody\n",
        encoding="utf-8",
    )
    services = _FakeServices()
    services.indexing_coordinator.raise_on_remove = True
    store = MemoryStoreService(memory_dir)

    result = await store.archive(
        services=services, path_or_id="lessons/gone.md"  # type: ignore[arg-type]
    )
    assert result.archived_path.startswith("archive/")
    assert result.indexed is False
    assert result.error is not None
    assert "index cleanup failed" in result.error
    assert not entry.exists()


@pytest.mark.asyncio
async def test_list_and_archive_when_parent_named_archive(tmp_path: Path) -> None:
    """Parent path component 'archive' must not hide live entries."""
    memory_dir = tmp_path / "archive" / "memory"
    prefs = memory_dir / "preferences"
    prefs.mkdir(parents=True)
    entry = prefs / "live.md"
    entry.write_text(
        "---\ntype: user_preference\nid: live-1\n---\n\n## Live\n\nkeep me\n",
        encoding="utf-8",
    )
    store = MemoryStoreService(memory_dir)
    entries = await store.list_entries(limit=10)
    assert len(entries) == 1
    assert entries[0]["id"] == "live-1"

    services = _FakeServices()
    result = await store.archive(
        services=services, path_or_id="preferences/live.md"  # type: ignore[arg-type]
    )
    assert result.error != "Already archived"
    assert result.archived_path.startswith("archive/")
    assert _is_archived_relative(memory_dir, memory_dir / result.archived_path)


@pytest.mark.asyncio
async def test_list_entries_filters_type(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    prefs = memory_dir / "preferences"
    prefs.mkdir(parents=True)
    (prefs / "a.md").write_text(
        "---\ntype: user_preference\nid: a\n---\n\n## A\n\nx\n",
        encoding="utf-8",
    )
    skills = memory_dir / "skills"
    skills.mkdir()
    (skills / "b.md").write_text(
        "---\ntype: skill\nid: b\n---\n\n## B\n\ny\n",
        encoding="utf-8",
    )

    store = MemoryStoreService(memory_dir)
    entries = await store.list_entries(entry_type="skill", limit=10)
    assert len(entries) == 1
    assert entries[0]["type"] == "skill"


def test_decision_is_memory_type() -> None:
    assert "decision" in MEMORY_TYPES


def test_metadata_filter_boosts_project_and_decision() -> None:
    chunks = [
        {"metadata": {"type": "lesson", "project": "other"}, "content": "l"},
        {
            "metadata": {"type": "decision", "project": "chunkhound", "tags": ["lan"]},
            "content": "d",
        },
    ]
    filtered = filter_chunks_by_task_context(
        chunks, "decision project:chunkhound tag:lan"
    )
    assert filtered[0]["metadata"]["type"] == "decision"


def test_resolve_rejects_parent_traversal(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        _resolve_memory_file(memory_dir, "../secret.md")


def test_resolve_rejects_absolute_outside(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        _resolve_memory_file(memory_dir, str(outside.resolve()))


def test_resolve_by_exact_frontmatter_id(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    lessons = memory_dir / "lessons"
    lessons.mkdir(parents=True)
    target = lessons / "note.md"
    target.write_text(
        "---\ntype: lesson\nid: exact-id-99\n---\n\n"
        "## Note\n\nbody mentions id: other\n",
        encoding="utf-8",
    )
    resolved = _resolve_memory_file(memory_dir, "exact-id-99")
    assert resolved == target.resolve()
