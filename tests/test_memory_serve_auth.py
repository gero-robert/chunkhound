"""Contract tests for memory serve auth and process lock."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from chunkhound.mcp_server.memory_http import (
    build_token_auth_middleware,
    extract_bearer_or_header,
    tokens_match,
)
from chunkhound.services.memory.process_lock import (
    acquire_memory_lock,
    lock_path,
    release_memory_lock,
)


def test_tokens_match_constant_time() -> None:
    assert tokens_match("abc", "abc")
    assert not tokens_match("abc", "abd")
    assert not tokens_match(None, "abc")
    assert not tokens_match("ab", "abc")  # length mismatch


def test_extract_bearer_and_custom_header() -> None:
    class H(dict):
        def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
            for k, v in self.items():
                if k.lower() == key.lower():
                    return v
            return default

    headers = H({"Authorization": "Bearer secret-token"})
    assert extract_bearer_or_header(headers) == "secret-token"

    headers2 = H({"X-ChunkHound-Token": "custom"})
    assert extract_bearer_or_header(headers2) == "custom"


def test_auth_middleware_rejects_missing_token() -> None:
    async def ok(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/mcp", endpoint=ok),
            Route("/health", endpoint=ok),
        ],
        middleware=[Middleware(build_token_auth_middleware("test-token"))],
    )
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    denied = client.get("/mcp")
    assert denied.status_code == 401
    allowed = client.get(
        "/mcp", headers={"Authorization": "Bearer test-token"}
    )
    assert allowed.status_code == 200
    assert allowed.text == "ok"


def test_process_lock_blocks_second_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    path = acquire_memory_lock(memory_dir, mode="memory-serve")
    assert path == lock_path(memory_dir)
    assert path.is_file()

    monkeypatch.setattr(
        "chunkhound.services.memory.process_lock.pid_alive", lambda pid: True
    )
    monkeypatch.setattr(
        "chunkhound.services.memory.process_lock.process_create_time",
        lambda pid: 123.0,
    )
    # Force lock file create-time match so holder is considered live
    path.write_text(f"{path.read_text(encoding='utf-8').splitlines()[0]}\n123.0\nserve\n")

    with pytest.raises(RuntimeError, match="already owned"):
        acquire_memory_lock(memory_dir, mode="memory-mcp-stdio")

    release_memory_lock(path)


def test_process_lock_reclaims_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    path = lock_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("999999\n1.0\nold\n", encoding="utf-8")

    monkeypatch.setattr(
        "chunkhound.services.memory.process_lock.pid_alive", lambda pid: False
    )
    new_path = acquire_memory_lock(memory_dir, mode="memory-serve")
    assert new_path.is_file()
    release_memory_lock(new_path)
