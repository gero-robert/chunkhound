"""Single-owner process lock for the memory directory (stdio mcp + serve).

Only one process may open the memory DuckDB at a time. Local and remote
clients should connect to the running ``memory serve`` over HTTP rather than
starting a second process against the same directory.
"""

from __future__ import annotations

import atexit
import os
from pathlib import Path

from chunkhound.daemon.process import pid_alive, process_create_time

LOCK_FILE_NAME = "memory-owner.lock"


def lock_path(memory_dir: Path) -> Path:
    return memory_dir.resolve() / ".chunkhound" / LOCK_FILE_NAME


def _read_lock(path: Path) -> tuple[int, float | None, str]:
    """Return (pid, create_time_or_none, mode_label)."""
    text = path.read_text(encoding="utf-8").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return -1, None, "unknown"
    try:
        pid = int(lines[0])
    except ValueError:
        return -1, None, "unknown"
    create_time: float | None = None
    if len(lines) >= 2:
        try:
            create_time = float(lines[1])
        except ValueError:
            create_time = None
    mode = lines[2] if len(lines) >= 3 else "unknown"
    return pid, create_time, mode


def _lock_holder_alive(pid: int, create_time: float | None) -> bool:
    if pid <= 0 or not pid_alive(pid):
        return False
    if create_time is None:
        # Legacy pid-only locks: treat live pid as holder.
        return True
    current = process_create_time(pid)
    if current is None:
        # Cannot prove create-time; if pid is live, assume still held.
        return True
    # Allow small float noise across platforms.
    return abs(current - create_time) < 1.0


def acquire_memory_lock(memory_dir: Path, *, mode: str) -> Path:
    """Acquire exclusive ownership of *memory_dir*.

    Raises RuntimeError if another live process holds the lock.
    """
    path = lock_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file():
        old_pid, old_ctime, old_mode = _read_lock(path)
        if _lock_holder_alive(old_pid, old_ctime):
            raise RuntimeError(
                f"Memory directory already owned by pid={old_pid} ({old_mode}), "
                f"lock={path}. Use that process (prefer `chunkhound memory serve` "
                "and connect clients via HTTP — including on this machine). "
                "Do not start a second mcp/serve against the same directory."
            )
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Could not remove stale memory lock {path}: {exc}"
            ) from exc

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError as exc:
        raise RuntimeError(
            f"Memory directory lock race (lock={path}). "
            "Another memory mcp/serve is starting."
        ) from exc

    pid = os.getpid()
    ctime = process_create_time(pid)
    payload = f"{pid}\n{ctime if ctime is not None else ''}\n{mode}\n"
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)

    atexit.register(release_memory_lock, path, pid)
    return path


def release_memory_lock(path: Path, expected_pid: int | None = None) -> None:
    """Release lock if it still belongs to *expected_pid* (default: this process)."""
    pid = expected_pid if expected_pid is not None else os.getpid()
    try:
        if not path.is_file():
            return
        holder, _, _ = _read_lock(path)
        if holder == pid:
            path.unlink(missing_ok=True)
    except OSError:
        pass
