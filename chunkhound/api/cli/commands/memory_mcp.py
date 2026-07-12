"""Launch the dedicated Memory MCP stdio server."""

from __future__ import annotations

import argparse
import os
import sys

from chunkhound.services.memory.paths import (
    ENV_MEMORY_DIR,
    resolve_memory_dir,
    validate_memory_dir,
)
from chunkhound.services.memory.process_lock import (
    acquire_memory_lock,
    release_memory_lock,
)


async def memory_mcp_command(args: argparse.Namespace) -> None:
    """Run the Memory MCP server for the resolved global memory directory."""
    memory_dir = resolve_memory_dir(getattr(args, "dir", None))
    try:
        validate_memory_dir(memory_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    try:
        lock_path = acquire_memory_lock(memory_dir, mode="memory-mcp-stdio")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "If `chunkhound memory serve` is already running, connect this harness "
            "to its HTTP URL instead of starting stdio mcp on the same directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.environ[ENV_MEMORY_DIR] = str(memory_dir)
    os.environ["CHUNKHOUND_MCP_MODE"] = "1"
    os.environ["CHUNKHOUND_DAEMON_MODE"] = "false"

    args.path = memory_dir
    args.no_daemon = True

    try:
        from chunkhound.mcp_server.memory_stdio import main as memory_stdio_main

        await memory_stdio_main(args=args)
    finally:
        release_memory_lock(lock_path)
