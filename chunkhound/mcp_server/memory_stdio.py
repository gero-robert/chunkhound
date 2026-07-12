"""Stdio MCP server for ChunkHound Memory."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from chunkhound.mcp_server.memory_server import MemoryMCPServer
from chunkhound.mcp_server.stdio import (
    _respond_with_startup_error,
    _silence_loguru,
)
from chunkhound.services.memory.paths import resolve_memory_dir, validate_memory_dir
from chunkhound.utils.windows_constants import IS_WINDOWS


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
        server = MemoryMCPServer(config, memory_dir, args=args)
        await server.run_stdio()
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
