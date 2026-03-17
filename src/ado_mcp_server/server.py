"""ADO MCP Server — Streamable HTTP transport."""

from __future__ import annotations

import contextlib
import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ado_mcp_server.tools.core import register_core_tools
from ado_mcp_server.tools.repositories import register_repository_tools
from ado_mcp_server.tools.wiki import register_wiki_tools
from ado_mcp_server.tools.work import register_work_tools
from ado_mcp_server.tools.work_items import register_work_item_tools
from ado_mcp_server.utils.auth import get_org, validate_api_key
from ado_mcp_server.utils.jwt_validator import JWKSCache, validate_jwt

logger = logging.getLogger(__name__)


def _create_mcp() -> FastMCP:
    """Create a fresh FastMCP instance with tools registered."""
    mcp = FastMCP(
        "ado-mcp-server",
        stateless_http=True,
        json_response=True,
        # Disable DNS rebinding protection — the server runs behind Container Apps'
        # ingress proxy which sets the external hostname in the Host header.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    register_core_tools(mcp)
    register_work_item_tools(mcp)
    register_work_tools(mcp)
    register_repository_tools(mcp)
    register_wiki_tools(mcp)
    return mcp


class APIKeyMiddleware:
    """ASGI middleware that validates X-API-Key on MCP endpoints.

    Health endpoints (/, /health) are excluded so Container Apps
    probes still work without auth.
    """

    PROTECTED_PATHS = {"/mcp"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] in self.PROTECTED_PATHS:
            request = Request(scope, receive)
            error = validate_api_key(request)
            if error is not None:
                await error(scope, receive, send)
                return
        await self.app(scope, receive, send)


class JWTMiddleware:
    """ASGI middleware that validates JWT Bearer tokens on MCP endpoints.

    Disabled by default (JWT_VALIDATION_DISABLED=true). When enabled,
    validates RS256 JWTs against Azure AD's JWKS endpoint.

    PAT tokens (not starting with 'ey') bypass validation entirely.
    """

    PROTECTED_PATHS = {"/mcp"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._disabled = os.environ.get("JWT_VALIDATION_DISABLED", "true").lower() == "true"
        tenant_id = os.environ.get("JWT_TENANT_ID", "")
        self._audience = os.environ.get("JWT_AUDIENCE", "")
        self._tenant_id = tenant_id
        self._jwks_cache = JWKSCache(tenant_id) if tenant_id else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._disabled or scope["type"] != "http" or scope["path"] not in self.PROTECTED_PATHS:
            await self.app(scope, receive, send)
            return

        if not self._jwks_cache:
            logger.warning("JWT validation enabled but JWT_TENANT_ID not set — skipping")
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        auth_header = request.headers.get("authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif auth_header and not auth_header.startswith("Basic "):
            token = auth_header

        if not token:
            response = JSONResponse(
                {"error": "JWT validation failed", "detail": "No Bearer token provided"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        error = await validate_jwt(token, self._tenant_id, self._audience, self._jwks_cache)
        if error is not None:
            response = JSONResponse(
                {"error": "JWT validation failed", "detail": error},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def create_app() -> Starlette:
    """Create the ASGI app with auth middleware, health, and MCP routes."""
    mcp = _create_mcp()

    async def health(request: Request) -> JSONResponse:
        tools = list(mcp._tool_manager._tools.keys())
        return JSONResponse({"status": "ok", "org": get_org(), "tools": tools})

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            yield

    mcp_app = mcp.streamable_http_app()
    routes = [
        Route("/", health),
        Route("/health", health),
        *mcp_app.routes,
    ]
    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(APIKeyMiddleware),
            Middleware(JWTMiddleware),
        ],
    )


app = create_app()
