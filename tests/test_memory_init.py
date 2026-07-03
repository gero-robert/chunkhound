"""Contract tests for chunkhound memory init."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chunkhound.api.cli.commands.memory_init import _PROTOCOL_SNIPPET, memory_init_command
from chunkhound.services.memory.paths import ENV_MEMORY_DIR, resolve_memory_dir


@pytest.mark.asyncio
async def test_memory_init_creates_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory_dir = tmp_path / "memory"
    monkeypatch.setattr(
        "chunkhound.api.cli.commands.memory_init.resolve_memory_dir",
        lambda cli_dir=None: memory_dir,
    )
    monkeypatch.setattr(
        "chunkhound.api.cli.commands.memory_init.subprocess.run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0})(),
    )

    args = type("Args", (), {"dir": memory_dir})()
    await memory_init_command(args)

    assert (memory_dir / ".chunkhound.json").is_file()
    assert (memory_dir / "MEMORY_PROTOCOL.md").is_file()
    assert (memory_dir / "preferences" / "user_preference.md").is_file()
    assert (memory_dir / "skills" / "skill.md").is_file()
    assert (memory_dir / "lessons" / "lesson.md").is_file()

    protocol = (memory_dir / "MEMORY_PROTOCOL.md").read_text(encoding="utf-8")
    assert "memory_research" in protocol
    assert "memory_semantic_search" in protocol
    for line in _PROTOCOL_SNIPPET.split("\n"):
        assert line in protocol

    config = json.loads((memory_dir / ".chunkhound.json").read_text(encoding="utf-8"))
    assert config["indexing"]["chunker"] == "prose"

    preference_path = memory_dir / "preferences" / "user_preference.md"
    preference_path.write_text("custom preference content", encoding="utf-8")
    await memory_init_command(args)
    assert preference_path.read_text(encoding="utf-8") == "custom preference content"


def test_resolve_memory_dir_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "from-cli"
    env_dir = tmp_path / "from-env"

    assert resolve_memory_dir(cli_dir) == cli_dir.resolve()

    monkeypatch.setenv(ENV_MEMORY_DIR, str(env_dir))
    assert resolve_memory_dir(None) == env_dir.resolve()

    monkeypatch.delenv(ENV_MEMORY_DIR, raising=False)
    resolved = resolve_memory_dir(None)
    assert resolved.name == ".chunkhound-memory"