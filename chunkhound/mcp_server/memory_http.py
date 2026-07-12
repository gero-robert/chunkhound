"""Streamable HTTP front for the shared Memory MCP server."""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from chunkhound.mcp_server.memory_server import MemoryMCPServer
from chunkhound.mcp_server.stdio import _MCP_AVAILABLE


# Token comparison helpers (constant-time)
def tokens_match(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    candidate = provided.strip()
    # compare_digest requires equal length; unequal means no match.
    if len(candidate) != len(expected):
        return False
    return secrets.compare_digest(candidate, expected)


def extract_bearer_or_header(
    headers: Any,
    expected_header: str = "x-chunkhound-token",
) -> str | None:
    """Extract token from Authorization Bearer or custom header."""
    auth = None
    custom = None
    try:
        auth = headers.get("authorization") or headers.get("Authorization")
        custom = headers.get(expected_header) or headers.get(expected_header.title())
    except Exception:
        # starlette Headers support .get
        pass

    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    if isinstance(auth, str):
        value = auth.strip()
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        if value:
            return value
    return None


def build_token_auth_middleware(token: str) -> Callable:
    """Return Starlette middleware class that enforces the shared token."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    class TokenAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Callable) -> Response:
            # Allow unauthenticated health probe
            if request.url.path in {"/health", "/healthz"}:
                return await call_next(request)

            provided = extract_bearer_or_header(request.headers)
            if not tokens_match(provided, token):
                return JSONResponse(
                    {"error": "Unauthorized — provide Authorization: Bearer <token> "
                     "or X-ChunkHound-Token header"},
                    status_code=401,
                )
            return await call_next(request)

    return TokenAuthMiddleware


async def run_memory_http(
    server: MemoryMCPServer,
    *,
    host: str,
    port: int,
    token: str,
) -> None:
    """Serve Memory MCP over Streamable HTTP with token auth."""
    if not _MCP_AVAILABLE:
        raise RuntimeError("MCP SDK is not available")

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.types import Receive, Scope, Send

    # Allow loopback and the configured bind host for DNS-rebinding checks.
    allowed_hosts = ["127.0.0.1", "localhost", "localhost:*", "127.0.0.1:*"]
    if host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
        allowed_hosts.extend([host, f"{host}:*"])
    # When binding all interfaces, clients use LAN IPs — disable strict Host
    # checks only for that deployment mode (token auth still required).
    enable_dns_protection = host not in {"0.0.0.0", "::"}

    session_manager = StreamableHTTPSessionManager(
        app=server.server,
        event_store=None,
        json_response=False,
        stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=enable_dns_protection,
            allowed_hosts=allowed_hosts if enable_dns_protection else [],
        ),
    )

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def health(_request: Request) -> JSONResponse:
        # No memory_dir path — avoid unauthenticated filesystem disclosure.
        return JSONResponse(
            {
                "status": "ok",
                "service": "chunkhound-memory",
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with server.server_lifespan():
            async with session_manager.run():
                yield

    app = Starlette(
        routes=[
            Route("/health", endpoint=health, methods=["GET"]),
            Route("/healthz", endpoint=health, methods=["GET"]),
            Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
            Route("/mcp/", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
        ],
        middleware=[Middleware(build_token_auth_middleware(token))],
        lifespan=lifespan,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    uv_server = uvicorn.Server(config)
    await uv_server.serve()


def print_client_setup(host: str, port: int, token: str, memory_dir: Path) -> None:
    """Print operator-facing client configuration snippets to stderr."""
    import json
    import sys

    # Prefer LAN-facing URL placeholder when binding all interfaces
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}/mcp"
    if host in {"0.0.0.0", "::"}:
        url = f"http://<this-machine-lan-ip>:{port}/mcp"

    snippet = {
        "mcpServers": {
            "chunkhound-memory": {
                "url": url,
                "headers": {
                    "Authorization": f"Bearer {token}",
                },
            }
        }
    }
    lines = [
        "",
        "=" * 60,
        " ChunkHound Memory — LAN server ready",
        "=" * 60,
        f" Memory dir: {memory_dir}",
        f" Endpoint:   {url}",
        f" Health:     http://{display_host}:{port}/health",
        "",
        " Client config (headers may vary by harness):",
        json.dumps(snippet, indent=2),
        "",
        " Also accepted: header X-ChunkHound-Token: <token>",
        " Keep this token secret on your LAN.",
        "=" * 60,
        "",
    ]
    sys.stderr.write("\n".join(lines) + "\n")
    sys.stderr.flush()
