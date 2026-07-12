"""Path resolution for the global Memory MCP directory."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MEMORY_DIR_NAME = ".chunkhound-memory"
ENV_MEMORY_DIR = "CHUNKHOUND_MEMORY_DIR"


def default_memory_dir() -> Path:
    """Return the default global memory directory path."""
    return Path.home() / DEFAULT_MEMORY_DIR_NAME


def resolve_memory_dir(
    cli_dir: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve memory directory with CLI > env > default priority."""
    if cli_dir is not None:
        return Path(cli_dir).expanduser().resolve()

    environment = env if env is not None else os.environ
    env_dir = environment.get(ENV_MEMORY_DIR)
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    return default_memory_dir().resolve()


def validate_memory_dir(memory_dir: Path) -> None:
    """Raise FileNotFoundError when memory dir or config is missing."""
    if not memory_dir.exists():
        raise FileNotFoundError(
            f"Memory directory not found: {memory_dir}. "
            "Run `chunkhound memory init` first."
        )

    config_path = memory_dir / ".chunkhound.json"
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Memory config not found: {config_path}. "
            "Run `chunkhound memory init` first."
        )
