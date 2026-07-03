"""Memory MCP command argument parser."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast


def add_memory_subparser(subparsers: Any) -> argparse.ArgumentParser:
    """Add memory parent command with init and mcp subcommands."""
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
        help="Run the dedicated Memory MCP stdio server",
    )
    mcp_parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Memory directory (default: ~/.chunkhound-memory or CHUNKHOUND_MEMORY_DIR)",
    )

    return cast(argparse.ArgumentParser, memory_parser)