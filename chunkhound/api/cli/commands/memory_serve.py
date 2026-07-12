"""Serve the Memory MCP over Streamable HTTP for LAN multi-client access."""

from __future__ import annotations

import argparse
import os
import secrets
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

ENV_MEMORY_TOKEN = "CHUNKHOUND_MEMORY_TOKEN"
ENV_MEMORY_HOST = "CHUNKHOUND_MEMORY_HOST"
ENV_MEMORY_PORT = "CHUNKHOUND_MEMORY_PORT"


def _resolve_token(args: argparse.Namespace) -> str:
    token = getattr(args, "token", None) or os.environ.get(ENV_MEMORY_TOKEN)
    if token and str(token).strip():
        return str(token).strip()
    generated = secrets.token_hex(32)
    sys.stderr.write(
        f"No --token / {ENV_MEMORY_TOKEN} provided; generated a session token.\n"
        f"Token: {generated}\n"
        "Pass this token to clients via Authorization: Bearer <token>.\n"
    )
    sys.stderr.flush()
    return generated


def _resolve_host_port(args: argparse.Namespace) -> tuple[str, int]:
    host = (
        getattr(args, "host", None)
        or os.environ.get(ENV_MEMORY_HOST)
        or "127.0.0.1"
    )
    port_raw = getattr(args, "port", None)
    if port_raw is None:
        port_raw = os.environ.get(ENV_MEMORY_PORT, "8765")
    try:
        port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid port: {port_raw}") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"Port out of range: {port}")
    return str(host), port


async def memory_serve_command(args: argparse.Namespace) -> None:
    """Run the Memory MCP Streamable HTTP server."""
    memory_dir = resolve_memory_dir(getattr(args, "dir", None))
    try:
        validate_memory_dir(memory_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    try:
        host, port = _resolve_host_port(args)
        token = _resolve_token(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if host in {"0.0.0.0", "::"} and not (
        getattr(args, "token", None) or os.environ.get(ENV_MEMORY_TOKEN)
    ):
        sys.stderr.write(
            "WARNING: Binding to all interfaces with a generated token. "
            "Prefer setting CHUNKHOUND_MEMORY_TOKEN for a stable secret.\n"
        )
        sys.stderr.flush()

    os.environ[ENV_MEMORY_DIR] = str(memory_dir)
    os.environ["CHUNKHOUND_MCP_MODE"] = "1"
    os.environ["CHUNKHOUND_DAEMON_MODE"] = "false"

    try:
        lock_path = acquire_memory_lock(memory_dir, mode="memory-serve")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    args.path = memory_dir
    args.no_daemon = True

    from chunkhound.api.cli.utils.config_factory import create_validated_config
    from chunkhound.mcp_server.memory_http import print_client_setup, run_memory_http
    from chunkhound.mcp_server.memory_server import MemoryMCPServer
    from chunkhound.mcp_server.stdio import _respond_with_startup_error, _silence_loguru

    _silence_loguru()

    config, validation_errors = create_validated_config(args, "mcp")
    if validation_errors:
        release_memory_lock(lock_path)
        msg = "; ".join(str(error) for error in validation_errors)
        _respond_with_startup_error(Exception(f"Configuration errors: {msg}"), config)
        sys.exit(1)

    try:
        server = MemoryMCPServer(config, memory_dir, args=args)
        print_client_setup(host, port, token, memory_dir)
        sys.stderr.write(
            "Single-owner mode: this process owns the memory DB. "
            "On this machine and others, attach harnesses via the HTTP URL above "
            "(do not also run `chunkhound memory mcp` against the same dir).\n"
        )
        sys.stderr.flush()
        await run_memory_http(server, host=host, port=port, token=token)
    except Exception as exc:
        _respond_with_startup_error(exc, config)
        sys.exit(1)
    finally:
        release_memory_lock(lock_path)
