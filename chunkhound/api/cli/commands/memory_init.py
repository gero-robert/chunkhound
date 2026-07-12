"""Initialize the global ChunkHound memory directory."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from chunkhound.services.memory.paths import (
    ENV_MEMORY_DIR,
    resolve_memory_dir,
)


def _template_dir() -> Path:
    try:
        from importlib.resources import files

        return Path(str(files("chunkhound.services.memory").joinpath("templates")))
    except Exception:
        return Path(__file__).resolve().parents[3] / "services" / "memory" / "templates"


_PROTOCOL_SNIPPET = (
    "ALWAYS start by calling memory_research with a query derived from the current "
    "user request plus any known user preferences.\n"
    "After important decisions, patterns, failures, or durable prefs, call "
    "memory_store so other machines/sessions share the knowledge.\n"
    "Prefer memory_research first (summarized insight); use memory_semantic_search "
    "only when you need raw entries.\n"
    "Use memory_archive for obsolete entries that mislead recall.\n"
    "Store pointers and trade-offs, not large source dumps."
)


def _memory_config(memory_dir: Path) -> dict[str, object]:
    db_path = memory_dir / ".chunkhound" / "db"
    return {
        "database": {"path": str(db_path), "provider": "duckdb"},
        "indexing": {
            "chunker": "prose",
            "include": ["*.md", "*.txt", "*.html"],
            "index_unknown_files": True,
            "exclude": [".chunkhound/**", "archive/**"],
        },
    }


def _copy_templates(memory_dir: Path) -> None:
    template_dir = _template_dir()
    for subdir in ("preferences", "skills", "lessons", "decisions", "archive"):
        (memory_dir / subdir).mkdir(parents=True, exist_ok=True)

    seed_files = (
        ("MEMORY_PROTOCOL.md", memory_dir / "MEMORY_PROTOCOL.md"),
        ("user_preference.md", memory_dir / "preferences" / "user_preference.md"),
        ("skill.md", memory_dir / "skills" / "skill.md"),
        ("lesson.md", memory_dir / "lessons" / "lesson.md"),
        ("decision.md", memory_dir / "decisions" / "decision.md"),
    )
    for filename, destination in seed_files:
        if not destination.exists():
            src = template_dir / filename
            if src.exists():
                shutil.copy2(src, destination)


def _print_setup_instructions(memory_dir: Path) -> None:
    memory_dir_str = str(memory_dir).replace("\\", "/")
    print(f"Memory directory ready: {memory_dir}")
    print("\nConfigure embeddings and LLM in .chunkhound.json, then choose a mode:\n")
    print("1) Local stdio (single machine):")
    print(
        json.dumps(
            {
                "mcpServers": {
                    "chunkhound-memory": {
                        "command": "uv",
                        "args": ["run", "chunkhound", "memory", "mcp"],
                        "env": {ENV_MEMORY_DIR: memory_dir_str},
                    }
                }
            },
            indent=2,
        )
    )
    print("\n2) LAN shared server (recommended multi-computer):")
    print(
        f"  chunkhound memory serve --dir {memory_dir_str} "
        "--host 0.0.0.0 --port 8765 --token <secret>"
    )
    print("  Clients use the printed URL + Authorization Bearer header.")
    print("\nAgent protocol (also in MEMORY_PROTOCOL.md):")
    print(_PROTOCOL_SNIPPET)


async def memory_init_command(args: argparse.Namespace) -> None:
    """Create the memory directory, config, templates, and initial index."""
    memory_dir = resolve_memory_dir(getattr(args, "dir", None))
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / ".chunkhound" / "db").mkdir(parents=True, exist_ok=True)

    config_path = memory_dir / ".chunkhound.json"
    if not config_path.exists():
        config_path.write_text(
            json.dumps(_memory_config(memory_dir), indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        # Ensure archive/** is excluded if config already exists
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
            indexing = existing.setdefault("indexing", {})
            exclude = list(indexing.get("exclude") or [])
            if "archive/**" not in exclude:
                exclude.append("archive/**")
                indexing["exclude"] = exclude
                config_path.write_text(
                    json.dumps(existing, indent=2) + "\n", encoding="utf-8"
                )
        except (json.JSONDecodeError, OSError):
            pass

    _copy_templates(memory_dir)

    index_cmd = [
        sys.executable,
        "-m",
        "chunkhound.api.cli.main",
        "index",
        str(memory_dir),
        "--no-embeddings",
        "--config",
        str(config_path),
        "--db",
        str(memory_dir / ".chunkhound" / "db"),
    ]
    result = subprocess.run(index_cmd, check=False)
    if result.returncode != 0:
        print(
            "Memory files created, but initial index failed. "
            "Configure providers and run:\n"
            f"  chunkhound index {memory_dir} --config {config_path}",
            file=sys.stderr,
        )
    else:
        print("Initial memory index completed (no embeddings).")

    _print_setup_instructions(memory_dir)
