"""Serve the Memory MCP over Streamable HTTP for LAN multi-client access."""

from __future__ import annotations

import argparse
import atexit
import os
import secrets
import sys
from pathlib import Path

from chunkhound.services.memory.paths import (
    ENV_MEMORY_DIR,
    resolve_memory_dir,
    validate_memory_dir,
)

ENV_MEMORY_TOKEN = "CHUNKHOUND_MEMORY_TOKEN"
ENV_MEMORY_HOST = "CHUNKHOUND_MEMORY_HOST"
ENV_MEMORY_PORT = "CHUNKHOUND_MEMORY_PORT"
PID_FILE_NAME = "memory-serve.pid"


def _pid_file(memory_dir: Path) -> Path:
    return memory_dir / ".chunkhound" / PID_FILE_NAME


def _write_pid_file(memory_dir: Path) -> Path:
    """Create an exclusive PID lock file, reclaiming only stale locks."""
    from chunkhound.daemon.process import pid_alive

    path = _pid_file(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # If a lock exists and the process is still alive, refuse.
    if path.is_file():
        try:
            old_pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = -1
        if old_pid > 0 and pid_alive(old_pid):
            raise RuntimeError(
                f"Another memory serve appears to be running (pid={old_pid}, "
                f"pidfile={path}). Stop it before starting a new server."
            )
        # Stale lock — remove before exclusive create
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Could not remove stale memory serve pidfile {path}: {exc}"
            ) from exc

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError as exc:
        raise RuntimeError(
            f"Another memory serve is starting (pidfile={path}). "
            "Stop it before starting a new server."
        ) from exc
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _remove_pid_file(path: Path) -> None:
    try:
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content == str(os.getpid()):
                path.unlink(missing_ok=True)
    except OSError:
        pass


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
        # Generated token is fine, but warn about LAN exposure
        sys.stderr.write(
            "WARNING: Binding to all interfaces with a generated token. "
            "Prefer setting CHUNKHOUND_MEMORY_TOKEN for a stable secret.\n"
        )
        sys.stderr.flush()

    os.environ[ENV_MEMORY_DIR] = str(memory_dir)
    os.environ["CHUNKHOUND_MCP_MODE"] = "1"
    os.environ["CHUNKHOUND_DAEMON_MODE"] = "false"

    try:
        pid_path = _write_pid_file(memory_dir)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    atexit.register(_remove_pid_file, pid_path)

    args.path = memory_dir
    args.no_daemon = True

    from chunkhound.api.cli.utils.config_factory import create_validated_config
    from chunkhound.mcp_server.memory_http import print_client_setup, run_memory_http
    from chunkhound.mcp_server.memory_server import MemoryMCPServer
    from chunkhound.mcp_server.stdio import _respond_with_startup_error, _silence_loguru

    _silence_loguru()

    config, validation_errors = create_validated_config(args, "mcp")
    if validation_errors:
        msg = "; ".join(str(error) for error in validation_errors)
        _respond_with_startup_error(Exception(f"Configuration errors: {msg}"), config)
        sys.exit(1)

    try:
        server = MemoryMCPServer(config, memory_dir, args=args)
        print_client_setup(host, port, token, memory_dir)
        await run_memory_http(server, host=host, port=port, token=token)
    except Exception as exc:
        _respond_with_startup_error(exc, config)
        sys.exit(1)
    finally:
        _remove_pid_file(pid_path)
