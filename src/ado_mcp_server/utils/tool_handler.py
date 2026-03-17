"""Decorator for consistent error handling across all MCP tool functions."""

from __future__ import annotations

import functools
import json

import httpx

MAX_ERROR_LENGTH = 500  # Truncate ADO error bodies to protect LLM token context


def handle_tool_errors(func):
    """Decorator for MCP tool functions. Catches HTTPStatusError and returns
    structured JSON. Truncates error messages to avoid blowing LLM context."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            message = e.response.text[:MAX_ERROR_LENGTH]
            if len(e.response.text) > MAX_ERROR_LENGTH:
                message += "... (truncated)"
            return json.dumps(
                {
                    "error": True,
                    "status": e.response.status_code,
                    "message": message,
                },
                indent=2,
            )

    return wrapper
