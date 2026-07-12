"""Contract tests for Memory MCP tool surface."""

from __future__ import annotations

import json

import pytest

from chunkhound.mcp_server.memory_tool_descriptions import (
    build_memory_research_description,
    build_memory_store_description,
)
from chunkhound.mcp_server.memory_tools import (
    MEMORY_TOOL_REGISTRY,
    set_memory_tool_descriptions,
)
from chunkhound.services.memory.paths import ENV_MEMORY_DIR, resolve_memory_dir

_EXPECTED_TOOLS = {
    "memory_research",
    "memory_semantic_search",
    "memory_store",
    "memory_archive",
    "memory_list",
}

_PROTOCOL_LINE = "ALWAYS start by calling memory_research"


def test_memory_mcp_exposes_expected_tools() -> None:
    set_memory_tool_descriptions("/tmp/memory")
    assert set(MEMORY_TOOL_REGISTRY.keys()) == _EXPECTED_TOOLS


def test_memory_tool_descriptions_contain_protocol(tmp_path) -> None:
    memory_dir = str(tmp_path / "memory")
    research = build_memory_research_description(memory_dir)
    store = build_memory_store_description(memory_dir)

    assert _PROTOCOL_LINE in research
    assert memory_dir in research
    assert memory_dir in store
    assert "memory_store" in store or "type" in store


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
