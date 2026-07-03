"""Stdio MCP server for ChunkHound Memory (two tools only)."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

from chunkhound.core.config.config import Config
from chunkhound.mcp_server.common import first_error_tool_content, has_reranker_support
from chunkhound.mcp_server.memory_tools import (
    MEMORY_TOOL_REGISTRY,
    execute_memory_tool,
    set_memory_tool_descriptions,
)
from chunkhound.mcp_server.stdio import (
    StdioMCPServer,
    _MCP_AVAILABLE,
    _respond_with_startup_error,
    _silence_loguru,
)
from chunkhound.services.memory.paths import resolve_memory_dir, validate_memory_dir
from chunkhound.utils.windows_constants import IS_WINDOWS
from chunkhound.version import __version__

if _MCP_AVAILABLE:
    import mcp.types as types


class MemoryStdioMCPServer(StdioMCPServer):
    """MCP server exposing only memory_research and memory_semantic_search."""

    def __init__(self, config: Config, memory_dir: Path, args: Any = None):
        self.memory_dir = memory_dir.resolve()
        set_memory_tool_descriptions(str(self.memory_dir))
        super().__init__(config, args=args)
        if _MCP_AVAILABLE:
            self.server = self._create_memory_server()
            self._register_tools()

    def _create_memory_server(self) -> Any:
        from mcp.server import Server

        return Server("ChunkHound Memory")

    def _build_filtered_tool_dicts(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for tool_name, tool in MEMORY_TOOL_REGISTRY.items():
            if tool.requires_embeddings and (
                not self.embedding_manager
                or not self.embedding_manager.list_providers()
            ):
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
                await asyncio.wait_for(self._initialization_complete.wait(), timeout=5.0)
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
                text = result if isinstance(result, str) else json.dumps(result, default=str)
                text_contents = [mcp_types.TextContent(type="text", text=text)]
                error_content = first_error_tool_content(text_contents)
                if error_content is not None:
                    raise RuntimeError(error_content.text)
                return text_contents
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc

        self._register_list_tools()

    async def run(self) -> None:
        if not _MCP_AVAILABLE:
            raise RuntimeError("MCP SDK is not available")

        from mcp.server.lowlevel import NotificationOptions
        from mcp.server.models import InitializationOptions
        import mcp.server.stdio

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


async def main(args: Any = None) -> None:
    """Entry point for memory MCP stdio server."""
    _silence_loguru()
    os.environ["CHUNKHOUND_MCP_MODE"] = "1"

    import argparse

    from chunkhound.api.cli.utils.config_factory import create_validated_config

    if args is None:
        parser = argparse.ArgumentParser(description="ChunkHound Memory MCP server")
        parser.add_argument("--dir", type=Path, default=None)
        parser.add_argument("--debug", action="store_true")
        args = parser.parse_args()

    memory_dir = resolve_memory_dir(getattr(args, "dir", None))
    validate_memory_dir(memory_dir)
    args.path = memory_dir

    config, validation_errors = create_validated_config(args, "mcp")
    if validation_errors:
        msg = "; ".join(str(error) for error in validation_errors)
        _respond_with_startup_error(Exception(f"Configuration errors: {msg}"), config)
        sys.exit(1)

    try:
        server = MemoryStdioMCPServer(config, memory_dir, args=args)
        await server.run()
    except Exception as exc:
        _respond_with_startup_error(exc, config)
        sys.exit(1)


def main_sync() -> None:
    if IS_WINDOWS:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="backslashreplace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(errors="backslashreplace")
    asyncio.run(main())