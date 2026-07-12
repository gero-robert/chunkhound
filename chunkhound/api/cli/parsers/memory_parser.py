"""Memory MCP command argument parser."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast


def add_memory_subparser(subparsers: Any) -> argparse.ArgumentParser:
    """Add memory parent command with init, mcp, and serve subcommands."""
    memory_parser = subparsers.add_parser(
        "memory",
        help="Manage ChunkHound agent memory",
        description="Initialize and serve the global agent memory knowledge base",
    )
    memory_subparsers = memory_parser.add_subparsers(
        dest="memory_command",
        required=True,
        help="Memory operations",
    )

    init_parser = memory_subparsers.add_parser(
        "init",
        help="Create the global memory directory and templates",
    )
    init_parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Memory directory (default: ~/.chunkhound-memory or CHUNKHOUND_MEMORY_DIR)",
    )

    mcp_parser = memory_subparsers.add_parser(
        "mcp",
        help="Run the dedicated Memory MCP stdio server (local/debug)",
    )
    mcp_parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Memory directory (default: ~/.chunkhound-memory or CHUNKHOUND_MEMORY_DIR)",
    )

    serve_parser = memory_subparsers.add_parser(
        "serve",
        help="Run Memory MCP over Streamable HTTP for LAN multi-client access",
    )
    serve_parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Memory directory (default: ~/.chunkhound-memory or CHUNKHOUND_MEMORY_DIR)",
    )
    serve_parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Bind host (default: 127.0.0.1 or CHUNKHOUND_MEMORY_HOST). "
        "Use 0.0.0.0 for LAN access.",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: 8765 or CHUNKHOUND_MEMORY_PORT)",
    )
    serve_parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Shared API token (or CHUNKHOUND_MEMORY_TOKEN). "
        "Required for clients via Authorization Bearer header.",
    )

    return cast(argparse.ArgumentParser, memory_parser)
