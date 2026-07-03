"""Memory MCP tool registry — exactly two tools, isolated from code MCP."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from chunkhound.core.config.config import Config
from chunkhound.database_factory import DatabaseServices
from chunkhound.embeddings import EmbeddingManager
from chunkhound.llm_manager import LLMManager
from chunkhound.mcp_server.memory_tool_descriptions import (
    build_memory_research_description,
    build_memory_semantic_search_description,
)
from chunkhound.mcp_server.tools import (
    Tool,
    _generate_json_schema_from_signature,
    estimate_tokens,
    format_search_results_markdown,
)
from chunkhound.services.memory.memory_research_service import MemoryResearchService

MEMORY_TOOL_REGISTRY: dict[str, Tool] = {}
_MEMORY_RESEARCH = MemoryResearchService()


def register_memory_tool(
    description: str,
    *,
    requires_embeddings: bool = False,
    requires_llm: bool = False,
    requires_reranker: bool = False,
    name: str | None = None,
) -> Callable[[Callable], Callable]:
    """Register a tool in the memory-only registry."""

    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        MEMORY_TOOL_REGISTRY[tool_name] = Tool(
            name=tool_name,
            description=description,
            parameters=_generate_json_schema_from_signature(func),
            implementation=func,
            requires_embeddings=requires_embeddings,
            requires_llm=requires_llm,
            requires_reranker=requires_reranker,
            requires_db="services" in inspect.signature(func).parameters,
        )
        return func

    return decorator


def set_memory_tool_descriptions(memory_dir: str) -> None:
    """Inject resolved memory directory into tool descriptions."""
    descriptions = {
        "memory_research": build_memory_research_description(memory_dir),
        "memory_semantic_search": build_memory_semantic_search_description(memory_dir),
    }
    for tool_name, description in descriptions.items():
        tool = MEMORY_TOOL_REGISTRY.get(tool_name)
        if tool is not None:
            MEMORY_TOOL_REGISTRY[tool_name] = Tool(
                name=tool.name,
                description=description,
                parameters=tool.parameters,
                implementation=tool.implementation,
                requires_embeddings=tool.requires_embeddings,
                requires_llm=tool.requires_llm,
                requires_reranker=tool.requires_reranker,
                requires_db=tool.requires_db,
            )


@register_memory_tool(
    description="placeholder",
    requires_embeddings=True,
    requires_llm=False,
    requires_reranker=False,
    name="memory_research",
)
async def memory_research_impl(
    services: DatabaseServices,
    embedding_manager: EmbeddingManager,
    llm_manager: LLMManager | None,
    query: str,
    task_context: str = "",
    config: Config | None = None,
) -> dict[str, Any]:
    """Summarize relevant memory for the current task."""
    if not embedding_manager or not embedding_manager.list_providers():
        raise RuntimeError(
            "memory_research requires an embedding provider. "
            "Configure embeddings in the memory directory .chunkhound.json."
        )

    result = await _MEMORY_RESEARCH.research(
        query=query,
        task_context=task_context,
        services=services,
        embedding_manager=embedding_manager,
        llm_manager=llm_manager,
    )
    return result


@register_memory_tool(
    description="placeholder",
    requires_embeddings=True,
    name="memory_semantic_search",
)
async def memory_semantic_search_impl(
    services: DatabaseServices,
    embedding_manager: EmbeddingManager,
    query: str,
) -> dict[str, Any]:
    """Return raw semantic memory hits."""
    if not embedding_manager or not embedding_manager.list_providers():
        raise RuntimeError(
            "memory_semantic_search requires an embedding provider. "
            "Configure embeddings in the memory directory .chunkhound.json."
        )

    results, pagination = await services.search_service.search_semantic(
        query=query,
        page_size=10,
        offset=0,
    )
    return {"results": results, "pagination": pagination}


def format_memory_semantic_markdown(
    results: list[dict[str, Any]],
    pagination: dict[str, Any],
) -> str:
    """Render semantic memory hits with metadata preserved."""
    if not results:
        return "No memory entries found."

    blocks: list[str] = []
    for result in results:
        metadata = result.get("metadata") or {}
        base = format_search_results_markdown([result], pagination, "semantic")
        if metadata:
            meta_json = json.dumps(metadata, ensure_ascii=False, indent=2)
            blocks.append(f"{base}\n\n**metadata:**\n```json\n{meta_json}\n```")
        else:
            blocks.append(base)
    return "\n\n".join(blocks)


async def execute_memory_tool(
    tool_name: str,
    *,
    services: DatabaseServices,
    embedding_manager: EmbeddingManager | None,
    llm_manager: LLMManager | None,
    arguments: dict[str, Any],
    config: Config | None = None,
) -> dict[str, Any] | str:
    """Execute a memory MCP tool."""
    if tool_name not in MEMORY_TOOL_REGISTRY:
        raise ValueError(f"Unknown memory tool: {tool_name}")

    tool = MEMORY_TOOL_REGISTRY[tool_name]
    sig = inspect.signature(tool.implementation)
    kwargs: dict[str, Any] = {}

    for param_name in sig.parameters:
        if param_name == "services":
            kwargs["services"] = services
        elif param_name == "embedding_manager":
            kwargs["embedding_manager"] = embedding_manager
        elif param_name == "llm_manager":
            kwargs["llm_manager"] = llm_manager
        elif param_name == "config":
            kwargs["config"] = config
        elif param_name in arguments:
            kwargs[param_name] = arguments[param_name]

    result = await tool.implementation(**kwargs)

    if tool_name == "memory_research" and isinstance(result, dict):
        return str(result.get("answer", "No memory summary available."))

    if tool_name == "memory_semantic_search" and isinstance(result, dict):
        results = list(result.get("results", []))
        pagination = dict(result.get("pagination", {}))
        markdown = format_memory_semantic_markdown(results, pagination)
        while len(results) > 1 and estimate_tokens(markdown) > 20000:
            results = results[:-1]
            markdown = format_memory_semantic_markdown(results, pagination)
        return markdown

    return result if isinstance(result, (dict, str)) else {"result": result}