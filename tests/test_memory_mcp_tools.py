"""Contract tests for Memory MCP tool surface and agent protocol."""

from __future__ import annotations

import json

import pytest

from chunkhound.mcp_server.memory_tool_descriptions import (
    build_memory_archive_description,
    build_memory_research_description,
    build_memory_store_description,
)
from chunkhound.mcp_server.memory_tools import (
    MEMORY_TOOL_REGISTRY,
    set_memory_tool_descriptions,
)
from chunkhound.services.memory.agent_protocol import (
    TOOL_PROTOCOL_SUMMARY,
    build_server_instructions,
    protocol_snippet_for_init,
)
from chunkhound.services.memory.paths import ENV_MEMORY_DIR, resolve_memory_dir

_EXPECTED_TOOLS = {
    "memory_research",
    "memory_semantic_search",
    "memory_store",
    "memory_archive",
    "memory_list",
}


def test_memory_mcp_exposes_expected_tools() -> None:
    set_memory_tool_descriptions("/tmp/memory")
    assert set(MEMORY_TOOL_REGISTRY.keys()) == _EXPECTED_TOOLS


def test_memory_tool_descriptions_contain_shared_policy(tmp_path) -> None:
    memory_dir = str(tmp_path / "memory")
    research = build_memory_research_description(memory_dir)
    store = build_memory_store_description(memory_dir)
    archive = build_memory_archive_description(memory_dir)

    for description in (research, store, archive):
        assert "MEMORY USAGE POLICY" in description
        assert "user approval" in description.lower() or "approval" in description
        assert memory_dir in description

    assert "Session start" in TOOL_PROTOCOL_SUMMARY or "Session start" in research
    assert "memory_store" in store
    assert "memory_archive" in archive


def test_server_instructions_cover_lifecycle() -> None:
    text = build_server_instructions("/tmp/mem")
    # Session lifecycle
    assert "Session start" in text
    assert "Mid-task" in text
    assert "memory_research" in text
    # Skills approval gate
    assert "approval" in text.lower()
    assert "skill" in text.lower()
    # Correct / delete
    assert "memory_archive" in text
    assert "Supersedes" in text or "supersede" in text.lower()
    # Anti-patterns
    assert "secret" in text.lower()
    assert "/tmp/mem" in text


def test_init_snippet_is_actionable() -> None:
    snippet = protocol_snippet_for_init()
    assert "memory_research" in snippet
    assert "skill" in snippet.lower()
    assert "memory_archive" in snippet or "archive" in snippet


def test_resolve_memory_dir_from_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_dir = tmp_path / "env-memory"
    env_dir.mkdir()
    monkeypatch.setenv(ENV_MEMORY_DIR, str(env_dir))
    assert resolve_memory_dir(None) == env_dir.resolve()


@pytest.mark.asyncio
async def test_memory_mcp_list_tools_via_registry() -> None:
    """Verify tool schemas expose expected parameters."""
    set_memory_tool_descriptions("/home/user/.chunkhound-memory")
    research = MEMORY_TOOL_REGISTRY["memory_research"]
    semantic = MEMORY_TOOL_REGISTRY["memory_semantic_search"]
    store = MEMORY_TOOL_REGISTRY["memory_store"]
    archive = MEMORY_TOOL_REGISTRY["memory_archive"]
    listing = MEMORY_TOOL_REGISTRY["memory_list"]

    research_props = research.parameters["properties"]
    assert "query" in research_props
    assert "task_context" in research_props
    assert "query" in research.parameters["required"]

    semantic_props = semantic.parameters["properties"]
    assert "query" in semantic_props
    assert json.dumps(semantic.parameters)

    store_props = store.parameters["properties"]
    assert "type" in store_props
    assert "title" in store_props
    assert "body" in store_props

    assert "path_or_id" in archive.parameters["properties"]
    assert "limit" in listing.parameters["properties"]
