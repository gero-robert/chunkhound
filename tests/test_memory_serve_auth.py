"""Contract tests for memory serve auth and pid guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from chunkhound.api.cli.commands.memory_serve import (
    _pid_file,
    _remove_pid_file,
    _write_pid_file,
)
from chunkhound.mcp_server.memory_http import (
    build_token_auth_middleware,
    extract_bearer_or_header,
    tokens_match,
)


def test_tokens_match_constant_time() -> None:
    assert tokens_match("abc", "abc")
    assert not tokens_match("abc", "abd")
    assert not tokens_match(None, "abc")


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


def test_pid_file_blocks_second_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    path = _write_pid_file(memory_dir)
    assert path == _pid_file(memory_dir)
    assert path.is_file()

    monkeypatch.setattr(
        "chunkhound.daemon.process.pid_alive", lambda pid: True
    )
    with pytest.raises(RuntimeError, match="Another memory serve"):
        _write_pid_file(memory_dir)

    _remove_pid_file(path)


def test_pid_file_reclaims_stale_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    path = _pid_file(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("999999", encoding="utf-8")

    monkeypatch.setattr(
        "chunkhound.daemon.process.pid_alive", lambda pid: False
    )
    new_path = _write_pid_file(memory_dir)
    assert new_path.is_file()
    assert new_path.read_text(encoding="utf-8").strip().isdigit()
    _remove_pid_file(new_path)
