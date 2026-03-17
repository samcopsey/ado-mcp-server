"""Auth token extraction and API key validation."""

from __future__ import annotations

import hmac
import os

from mcp.server.fastmcp import Context
from starlette.requests import Request
from starlette.responses import JSONResponse


def get_token(ctx: Context | None = None) -> str:
    """Extract OAuth token from MCP request headers, falling back to env var.

    Foundry passes the user's OAuth token via the Authorization header
    when using OAuth Identity Passthrough. For local dev, we fall back
    to AZURE_DEVOPS_PAT.
    """
    if ctx is not None:
        try:
            request: Request = ctx.request_context.request
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                return auth_header[7:]
            if auth_header:
                return auth_header
        except (AttributeError, TypeError, ValueError):
            pass
    return os.environ.get("AZURE_DEVOPS_PAT", "")


def get_org(ctx: Context | None = None) -> str:
    """Get the ADO organisation name."""
    return os.environ.get("AZURE_DEVOPS_ORG", "") or os.environ.get("ADO_ORG", "")


def validate_api_key(request: Request) -> JSONResponse | None:
    """Validate API key from header or query parameter.

    Checks X-API-Key header first, then falls back to ?api_key= query param.
    The query param approach exists because Foundry's MCP connection form
    doesn't support custom headers — the key is embedded in the endpoint URL.

    Returns None if valid, or a 401 JSONResponse if invalid.
    Skips validation if MCP_API_KEY is not configured (local dev).
    """
    expected_key = os.environ.get("MCP_API_KEY", "")
    if not expected_key:
        return None

    provided_key = (
        request.headers.get("x-api-key", "")
        or request.query_params.get("api_key", "")
    )
    if not provided_key or not hmac.compare_digest(provided_key, expected_key):
        return JSONResponse(
            {"error": "Invalid or missing API key"},
            status_code=401,
        )
    return None
