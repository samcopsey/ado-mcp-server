"""Tests for core ADO tools."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from ado_mcp_server.tools.core import register_core_tools


@pytest.fixture
def mcp_server() -> FastMCP:
    server = FastMCP("test-server", stateless_http=True, json_response=True)
    register_core_tools(server)
    return server


def parse_tool_result(result: tuple) -> dict:
    """Extract JSON from FastMCP call_tool result tuple."""
    contents, _meta = result
    return json.loads(contents[0].text)


@respx.mock
@pytest.mark.asyncio
async def test_core_list_projects(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/_apis/projects").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 2,
                "value": [
                    {"id": "p1", "name": "Project Alpha", "description": "First project", "state": "wellFormed"},
                    {"id": "p2", "name": "Project Beta", "description": "", "state": "wellFormed"},
                ],
            },
        )
    )

    result = await mcp_server.call_tool("core_list_projects", {})
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["projects"][0]["name"] == "Project Alpha"
    assert data["projects"][1]["id"] == "p2"


@respx.mock
@pytest.mark.asyncio
async def test_core_list_projects_with_filter(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/_apis/projects").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "value": [
                    {"id": "p1", "name": "Cast", "description": "Test", "state": "wellFormed"},
                ],
            },
        )
    )

    result = await mcp_server.call_tool("core_list_projects", {"projectNameFilter": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["projects"][0]["name"] == "Cast"


@respx.mock
@pytest.mark.asyncio
async def test_core_list_project_teams(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/_apis/projects/Cast/teams").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "value": [
                    {"id": "t1", "name": "Cast Team", "description": "Default team"},
                ],
            },
        )
    )

    result = await mcp_server.call_tool("core_list_project_teams", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["teams"][0]["name"] == "Cast Team"


@respx.mock
@pytest.mark.asyncio
async def test_core_get_identity_ids(mcp_server: FastMCP) -> None:
    respx.get("https://vssps.dev.azure.com/test-org/_apis/identities").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "value": [
                    {
                        "id": "id-123",
                        "providerDisplayName": "Sam Copsey",
                        "properties": {"Account": {"$value": "sam@example.com"}},
                    },
                ],
            },
        )
    )

    result = await mcp_server.call_tool("core_get_identity_ids", {"searchFilter": "sam"})
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["identities"][0]["displayName"] == "Sam Copsey"
    assert data["identities"][0]["uniqueName"] == "sam@example.com"


# --- Error handling (decorator) ---


@respx.mock
@pytest.mark.asyncio
async def test_core_list_projects_unauthorized(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/_apis/projects").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    result = await mcp_server.call_tool("core_list_projects", {})
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 401


@respx.mock
@pytest.mark.asyncio
async def test_core_get_identity_ids_error(mcp_server: FastMCP) -> None:
    respx.get("https://vssps.dev.azure.com/test-org/_apis/identities").mock(
        return_value=httpx.Response(404, text="Not found")
    )

    result = await mcp_server.call_tool("core_get_identity_ids", {"searchFilter": "nobody"})
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404
