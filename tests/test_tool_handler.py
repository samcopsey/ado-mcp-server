"""Tests for the handle_tool_errors decorator."""

from __future__ import annotations

import json

import httpx
import pytest

from ado_mcp_server.utils.tool_handler import MAX_ERROR_LENGTH, handle_tool_errors


@pytest.mark.asyncio
async def test_http_error_returns_json() -> None:
    """Decorator catches HTTPStatusError and returns structured JSON."""

    @handle_tool_errors
    async def failing_tool():
        resp = httpx.Response(403, text="Access denied", request=httpx.Request("GET", "https://example.com"))
        raise httpx.HTTPStatusError("error", request=resp.request, response=resp)

    result = await failing_tool()
    data = json.loads(result)

    assert data["error"] is True
    assert data["status"] == 403
    assert data["message"] == "Access denied"


@pytest.mark.asyncio
async def test_error_message_truncated() -> None:
    """Long error messages are truncated to MAX_ERROR_LENGTH."""
    long_text = "x" * 1000

    @handle_tool_errors
    async def failing_tool():
        resp = httpx.Response(500, text=long_text, request=httpx.Request("GET", "https://example.com"))
        raise httpx.HTTPStatusError("error", request=resp.request, response=resp)

    result = await failing_tool()
    data = json.loads(result)

    assert data["status"] == 500
    assert len(data["message"]) == MAX_ERROR_LENGTH + len("... (truncated)")
    assert data["message"].endswith("... (truncated)")


@pytest.mark.asyncio
async def test_non_http_error_propagates() -> None:
    """Non-HTTPStatusError exceptions are not caught by the decorator."""

    @handle_tool_errors
    async def failing_tool():
        raise ValueError("something went wrong")

    with pytest.raises(ValueError, match="something went wrong"):
        await failing_tool()


@pytest.mark.asyncio
async def test_decorator_preserves_function_metadata() -> None:
    """functools.wraps preserves the original function's name and docstring."""

    @handle_tool_errors
    async def my_tool():
        """My tool docstring."""
        return "ok"

    assert my_tool.__name__ == "my_tool"
    assert my_tool.__doc__ == "My tool docstring."


@pytest.mark.asyncio
async def test_successful_call_passes_through() -> None:
    """Successful calls return the original result unmodified."""

    @handle_tool_errors
    async def good_tool():
        return json.dumps({"result": "success"})

    result = await good_tool()
    assert json.loads(result)["result"] == "success"
