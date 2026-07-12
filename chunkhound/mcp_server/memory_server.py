"""Shared Memory MCP server core (stdio and HTTP fronts)."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

from chunkhound.core.config.config import Config
from chunkhound.mcp_server.common import first_error_tool_content, has_reranker_support
from chunkhound.mcp_server.memory_tools import (
    MEMORY_TOOL_REGISTRY,
    execute_memory_tool,
    set_memory_tool_descriptions,
)
from chunkhound.mcp_server.stdio import _MCP_AVAILABLE, StdioMCPServer
from chunkhound.version import __version__

if _MCP_AVAILABLE:
    import mcp.types as types


class MemoryMCPServer(StdioMCPServer):
    """MCP server exposing memory research/search/store tools."""

    def __init__(self, config: Config, memory_dir: Path, args: Any = None):
        self.memory_dir = memory_dir.resolve()
        set_memory_tool_descriptions(str(self.memory_dir))
        # Initialize base services without StdioMCPServer's "Code Search" Server
        # so we only register tools once on the Memory server instance.
        from chunkhound.mcp_server.base import MCPServerBase

        MCPServerBase.__init__(self, config, args=args)
        self._initialization_complete = asyncio.Event()
        if not _MCP_AVAILABLE:
            self.server = None  # type: ignore[assignment]
        else:
            from mcp.server import Server

            self.server = Server("ChunkHound Memory")
            self._register_tools()

    def _build_filtered_tool_dicts(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for tool_name, tool in MEMORY_TOOL_REGISTRY.items():
            if tool.requires_embeddings and (
                not self.embedding_manager
                or not self.embedding_manager.list_providers()
            ):
                # Still expose store/list/archive; only hide embedding-required tools
                continue
            if tool.requires_llm and not self.llm_manager:
                continue
            if tool.requires_reranker and not has_reranker_support(
                self.embedding_manager
            ):
                continue
            tools.append(
                {
                    "name": tool_name,
                    "description": tool.description,
                    "inputSchema": copy.deepcopy(tool.parameters),
                }
            )
        return tools

    def _register_tools(self) -> None:
        if not _MCP_AVAILABLE:
            return

        @self.server.call_tool()  # type: ignore[misc]
        async def handle_memory_tools(
            tool_name: str, arguments: dict[str, Any]
        ) -> list[types.TextContent]:
            import mcp.types as mcp_types

            try:
                await asyncio.wait_for(
                    self._initialization_complete.wait(), timeout=5.0
                )
                if tool_name not in MEMORY_TOOL_REGISTRY:
                    raise ValueError(f"Unknown memory tool: {tool_name}")

                tool = MEMORY_TOOL_REGISTRY[tool_name]
                if tool.requires_db:
                    services = await self.ensure_tool_services(tool_name)
                else:
                    services = self.services

                parsed_args = dict(arguments)
                result = await execute_memory_tool(
                    tool_name,
                    services=services,
                    embedding_manager=self.embedding_manager,
                    llm_manager=self.llm_manager,
                    arguments=parsed_args,
                    config=self.config,
                )
                if isinstance(result, str):
                    text = result
                else:
                    text = json.dumps(result, default=str)
                text_contents = [mcp_types.TextContent(type="text", text=text)]
                error_content = first_error_tool_content(text_contents)
                if error_content is not None:
                    raise RuntimeError(error_content.text)
                return text_contents
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc

        self._register_list_tools()

    async def run_stdio(self) -> None:
        """Run the server over stdio (local/debug)."""
        if not _MCP_AVAILABLE:
            raise RuntimeError("MCP SDK is not available")

        import mcp.server.stdio
        from mcp.server.lowlevel import NotificationOptions
        from mcp.server.models import InitializationOptions

        init_options = InitializationOptions(
            server_name="ChunkHound Memory",
            server_version=__version__,
            capabilities=self.server.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )

        async with self.server_lifespan():
            async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    initialization_options=init_options,
                )

    # Back-compat alias
    async def run(self) -> None:
        await self.run_stdio()
