"""Write and archive memory Markdown entries for the Memory MCP."""

from __future__ import annotations

import asyncio
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from chunkhound.database_factory import DatabaseServices
from chunkhound.parsers.frontmatter import MEMORY_TYPES

_TYPE_TO_DIR = {
    "user_preference": "preferences",
    "skill": "skills",
    "lesson": "lessons",
    "failure": "lessons",
    "decision": "decisions",
}

_TYPE_SHORT = {
    "user_preference": "pref",
    "skill": "skill",
    "lesson": "lesson",
    "failure": "fail",
    "decision": "dec",
}

_CONFIDENCE = frozenset({"low", "medium", "high"})
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WRITE_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class StoreResult:
    """Result of creating a memory entry on disk and indexing it."""

    path: str
    id: str
    type: str
    indexed: bool
    embeddings_ok: bool
    chunks: int
    error: str | None = None


@dataclass(frozen=True)
class ArchiveResult:
    """Result of archiving a memory entry."""

    path: str
    archived_path: str
    indexed: bool
    chunks_removed: int = 0
    error: str | None = None


def _slugify(title: str, max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    if not slug:
        slug = "entry"
    return slug[:max_len].rstrip("-")


def _subdir_for_type(entry_type: str) -> str:
    try:
        return _TYPE_TO_DIR[entry_type]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported memory type '{entry_type}'. "
            f"Expected one of: {', '.join(sorted(MEMORY_TYPES))}"
        ) from exc


def _is_archived_relative(memory_dir: Path, path: Path) -> bool:
    """True when *path* is under memory_dir/archive/ (relative check only)."""
    try:
        rel = path.resolve().relative_to(memory_dir.resolve())
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] == "archive"


def _render_markdown(
    *,
    entry_type: str,
    title: str,
    body: str,
    entry_id: str,
    applies_to: str,
    tags: list[str],
    confidence: str,
    project: str,
    source: str,
    learned_at: str,
) -> str:
    frontmatter: dict[str, Any] = {
        "type": entry_type,
        "learned_at": learned_at,
        "confidence": confidence,
        "id": entry_id,
    }
    if applies_to:
        frontmatter["applies_to"] = applies_to
    if tags:
        frontmatter["tags"] = tags
    if project:
        frontmatter["project"] = project
    if source:
        frontmatter["source"] = source

    yaml_block = yaml.safe_dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    body_text = body.strip()
    title_line = title.strip()
    if not title_line.startswith("#"):
        title_line = f"## {title_line}"
    return f"---\n{yaml_block}\n---\n\n{title_line}\n\n{body_text}\n"


def _normalize_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        parts = [part.strip() for part in tags.split(",")]
        return [part for part in parts if part]
    return [str(tag).strip() for tag in tags if str(tag).strip()]


def _ensure_inside_memory_dir(memory_dir: Path, path: Path) -> Path:
    """Resolve *path* and ensure it stays under *memory_dir*."""
    root = memory_dir.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path is outside the memory directory") from exc
    return resolved


def _entry_id_taken(memory_dir: Path, entry_id: str) -> bool:
    """True if any non-archive markdown uses this id as stem or frontmatter id."""
    root = memory_dir.resolve()
    for path in root.rglob("*.md"):
        if _is_archived_relative(root, path):
            continue
        if path.stem == entry_id:
            return True
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _peek_frontmatter(text)
        if meta.get("id") == entry_id:
            return True
    return False


def _resolve_memory_file(memory_dir: Path, path_or_id: str) -> Path:
    """Resolve a relative path or entry id to an existing memory file."""
    root = memory_dir.resolve()
    raw = path_or_id.strip()
    if not raw:
        raise ValueError("path_or_id is required")

    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = _ensure_inside_memory_dir(root, candidate)
        if resolved.is_file():
            return resolved
        raise FileNotFoundError(f"Memory file not found: {path_or_id}")

    # Reject explicit parent traversal in the relative form
    if ".." in Path(raw).parts:
        raise ValueError("Path is outside the memory directory")

    relative = _ensure_inside_memory_dir(root, root / raw)
    if relative.is_file():
        return relative

    # Match by frontmatter id or filename stem (exact); require uniqueness
    needle = raw
    matches: list[Path] = []
    for path in root.rglob("*.md"):
        try:
            resolved = _ensure_inside_memory_dir(root, path)
        except ValueError:
            continue
        if _is_archived_relative(root, resolved):
            continue
        hit = resolved.stem == needle or resolved.name == needle
        if not hit:
            try:
                text = resolved.read_text(encoding="utf-8")
            except OSError:
                continue
            meta = _peek_frontmatter(text)
            hit = meta.get("id") == needle
        if hit:
            matches.append(resolved)

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        paths = ", ".join(
            str(m.relative_to(root)).replace("\\", "/") for m in matches[:5]
        )
        raise ValueError(
            f"Ambiguous memory id {needle!r} matches multiple files: {paths}"
        )
    raise FileNotFoundError(f"Memory entry not found: {path_or_id}")


class MemoryStoreService:
    """Create and archive Markdown memory entries, then reindex them."""

    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir.resolve()

    async def store(
        self,
        *,
        services: DatabaseServices,
        entry_type: str,
        title: str,
        body: str,
        applies_to: str = "",
        tags: list[str] | str | None = None,
        confidence: str = "medium",
        project: str = "",
        source: str = "",
    ) -> StoreResult:
        if entry_type not in MEMORY_TYPES:
            raise ValueError(
                f"Unsupported memory type '{entry_type}'. "
                f"Expected one of: {', '.join(sorted(MEMORY_TYPES))}"
            )
        if not title.strip():
            raise ValueError("title is required")
        if not body.strip():
            raise ValueError("body is required")

        conf = (confidence or "medium").strip().lower()
        if conf not in _CONFIDENCE:
            raise ValueError("confidence must be one of: low, medium, high")

        tag_list = _normalize_tags(tags)
        subdir = _subdir_for_type(entry_type)
        target_dir = self.memory_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        type_tag = _TYPE_SHORT.get(entry_type, "mem")

        async with _WRITE_LOCK:
            now = datetime.now(timezone.utc)
            learned_at = now.date().isoformat()
            base_id = (
                f"{now.strftime('%Y%m%d-%H%M%S')}-"
                f"{now.microsecond // 1000:03d}-"
                f"{type_tag}-"
                f"{_slugify(title)}"
            )
            entry_id = base_id
            target_path = target_dir / f"{entry_id}.md"
            suffix = 0
            while target_path.exists() or _entry_id_taken(self.memory_dir, entry_id):
                suffix += 1
                # Random component avoids cross-type / cross-client races
                token = secrets.token_hex(2)
                entry_id = f"{base_id}-{suffix}-{token}"
                target_path = target_dir / f"{entry_id}.md"

            content = _render_markdown(
                entry_type=entry_type,
                title=title,
                body=body,
                entry_id=entry_id,
                applies_to=applies_to.strip(),
                tags=tag_list,
                confidence=conf,
                project=project.strip(),
                source=source.strip(),
                learned_at=learned_at,
            )
            tmp_path = target_path.with_suffix(".md.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(target_path)

        return await self._index_path(services, target_path, entry_id, entry_type)

    async def archive(
        self,
        *,
        services: DatabaseServices,
        path_or_id: str,
    ) -> ArchiveResult:
        if not path_or_id.strip():
            raise ValueError("path_or_id is required")

        async with _WRITE_LOCK:
            source = _resolve_memory_file(self.memory_dir, path_or_id)
            rel_src = str(source.relative_to(self.memory_dir)).replace("\\", "/")
            if _is_archived_relative(self.memory_dir, source):
                return ArchiveResult(
                    path=rel_src,
                    archived_path=rel_src,
                    indexed=True,
                    chunks_removed=0,
                    error="Already archived",
                )

            archive_dir = self.memory_dir / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / source.name
            if dest.exists():
                dest = archive_dir / f"{source.stem}-{now_stamp()}{source.suffix}"
            source.replace(dest)
            rel_dest = str(dest.relative_to(self.memory_dir)).replace("\\", "/")

        # Soft-delete from the index: remove old path rows. Do NOT reindex archive/.
        try:
            chunks_removed = await services.indexing_coordinator.remove_file(
                str(source),
                raise_on_error=True,
            )
            return ArchiveResult(
                path=rel_src,
                archived_path=rel_dest,
                indexed=True,
                chunks_removed=int(chunks_removed or 0),
                error=None,
            )
        except Exception as exc:
            return ArchiveResult(
                path=rel_src,
                archived_path=rel_dest,
                indexed=False,
                chunks_removed=0,
                error=(
                    f"File moved to {rel_dest} but index cleanup failed: {exc}"
                ),
            )

    async def list_entries(
        self,
        *,
        entry_type: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        files: list[Path] = []
        for path in self.memory_dir.rglob("*.md"):
            if path.name in {"MEMORY_PROTOCOL.md"}:
                continue
            if _is_archived_relative(self.memory_dir, path):
                continue
            if path.suffix == ".tmp":
                continue
            files.append(path)

        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        results: list[dict[str, Any]] = []
        type_filter = entry_type.strip().lower()
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta = _peek_frontmatter(text)
            if type_filter and str(meta.get("type", "")).lower() != type_filter:
                continue
            results.append(
                {
                    "path": str(path.relative_to(self.memory_dir)).replace("\\", "/"),
                    "id": meta.get("id") or path.stem,
                    "type": meta.get("type"),
                    "title": _first_heading(text) or path.stem,
                    "applies_to": meta.get("applies_to"),
                    "project": meta.get("project"),
                    "tags": meta.get("tags") or [],
                    "learned_at": meta.get("learned_at"),
                }
            )
            if len(results) >= limit:
                break
        return results

    async def _index_path(
        self,
        services: DatabaseServices,
        path: Path,
        entry_id: str,
        entry_type: str,
    ) -> StoreResult:
        rel = str(path.relative_to(self.memory_dir)).replace("\\", "/")
        try:
            result = await services.indexing_coordinator.process_file(
                path, skip_embeddings=False
            )
        except Exception as exc:
            return StoreResult(
                path=rel,
                id=entry_id,
                type=entry_type,
                indexed=False,
                embeddings_ok=False,
                chunks=0,
                error=str(exc),
            )

        status = str(result.get("status", ""))
        error = result.get("error")
        chunks = int(result.get("chunks") or result.get("chunk_count") or 0)

        embeddings_skipped = bool(result.get("embeddings_skipped"))
        embedding_error = result.get("embedding_error")
        embeddings_generated = int(result.get("embeddings_generated") or 0)
        # indexed chunks may exist without embeddings (no provider / embed error)
        embeddings_ok = (
            status == "success"
            and not embeddings_skipped
            and not embedding_error
            and embeddings_generated > 0
        )

        if status in {"error", "skipped"}:
            return StoreResult(
                path=rel,
                id=entry_id,
                type=entry_type,
                indexed=False,
                embeddings_ok=False,
                chunks=chunks,
                error=str(error or result.get("reason") or status),
            )

        return StoreResult(
            path=rel,
            id=entry_id,
            type=entry_type,
            indexed=status == "success",
            embeddings_ok=embeddings_ok,
            chunks=chunks,
            error=str(embedding_error)
            if embedding_error
            else (str(error) if error else None),
        )


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _peek_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    lines = text.split("\n")
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end_idx]))
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None
