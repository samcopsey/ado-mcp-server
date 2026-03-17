"""Tests for wiki tools."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from ado_mcp_server.tools.wiki import register_wiki_tools


@pytest.fixture
def mcp_server() -> FastMCP:
    server = FastMCP("test-server", stateless_http=True, json_response=True)
    register_wiki_tools(server)
    return server


def parse_tool_result(result: tuple) -> dict:
    """Extract JSON from FastMCP call_tool result tuple."""
    contents, _meta = result
    return json.loads(contents[0].text)


BASE = "https://dev.azure.com/test-org"


# --- wiki_list_wikis ---


@respx.mock
@pytest.mark.asyncio
async def test_wiki_list_wikis(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis").mock(
        return_value=httpx.Response(200, json={
            "value": [
                {"id": "wiki-1", "name": "Cast.wiki", "type": "projectWiki", "url": "https://example.com/wiki1"},
                {"id": "wiki-2", "name": "Code Wiki", "type": "codeWiki", "url": "https://example.com/wiki2"},
            ],
        })
    )

    result = await mcp_server.call_tool("wiki_list_wikis", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["wikis"][0]["name"] == "Cast.wiki"
    assert data["wikis"][0]["type"] == "projectWiki"
    assert data["wikis"][1]["type"] == "codeWiki"


# --- wiki_get_wiki ---


@respx.mock
@pytest.mark.asyncio
async def test_wiki_get_wiki(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis/wiki-1").mock(
        return_value=httpx.Response(200, json={
            "id": "wiki-1",
            "name": "Cast.wiki",
            "type": "projectWiki",
            "url": "https://example.com/wiki1",
            "versions": [{"version": "wikiMaster", "versionType": "branch"}],
            "repositoryId": "repo-abc",
            "mappedPath": "/",
        })
    )

    result = await mcp_server.call_tool("wiki_get_wiki", {"project": "Cast", "wiki_id": "wiki-1"})
    data = parse_tool_result(result)

    assert data["id"] == "wiki-1"
    assert data["name"] == "Cast.wiki"
    assert data["repositoryId"] == "repo-abc"
    assert len(data["versions"]) == 1
    assert data["versions"][0]["version"] == "wikiMaster"


# --- wiki_create_wiki ---


@respx.mock
@pytest.mark.asyncio
async def test_wiki_create_wiki(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/_apis/projects/Cast").mock(
        return_value=httpx.Response(200, json={"id": "proj-123", "name": "Cast"})
    )
    route = respx.post(f"{BASE}/Cast/_apis/wiki/wikis").mock(
        return_value=httpx.Response(201, json={
            "id": "wiki-new",
            "name": "Cast.wiki",
            "type": "projectWiki",
            "url": "https://example.com/wiki-new",
        })
    )

    result = await mcp_server.call_tool(
        "wiki_create_wiki", {"project": "Cast", "name": "Cast.wiki"}
    )
    data = parse_tool_result(result)

    assert data["id"] == "wiki-new"
    assert data["name"] == "Cast.wiki"
    assert data["type"] == "projectWiki"

    request_body = json.loads(route.calls[0].request.content)
    assert request_body["projectId"] == "proj-123"
    assert request_body["type"] == "projectWiki"


@respx.mock
@pytest.mark.asyncio
async def test_wiki_create_wiki_already_exists(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/_apis/projects/Cast").mock(
        return_value=httpx.Response(200, json={"id": "proj-123", "name": "Cast"})
    )
    respx.post(f"{BASE}/Cast/_apis/wiki/wikis").mock(
        return_value=httpx.Response(409, text="Wiki already exists for this project")
    )

    result = await mcp_server.call_tool(
        "wiki_create_wiki", {"project": "Cast", "name": "Cast.wiki"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 409


# --- wiki_list_pages ---


@respx.mock
@pytest.mark.asyncio
async def test_wiki_list_pages(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis/wiki-1/pages").mock(
        return_value=httpx.Response(200, json={
            "path": "/",
            "id": 1,
            "order": 0,
            "isParentPage": True,
            "subPages": [
                {
                    "path": "/Getting-Started",
                    "id": 2,
                    "order": 0,
                    "isParentPage": True,
                    "subPages": [
                        {
                            "path": "/Getting-Started/Installation", "id": 3,
                            "order": 0, "isParentPage": False, "subPages": [],
                        },
                    ],
                },
                {"path": "/API-Reference", "id": 4, "order": 1, "isParentPage": False, "subPages": []},
            ],
        })
    )

    result = await mcp_server.call_tool("wiki_list_pages", {"project": "Cast", "wiki_id": "wiki-1"})
    data = parse_tool_result(result)

    assert data["count"] == 4
    assert data["pages"][0]["path"] == "/"
    assert data["pages"][0]["depth"] == 0
    assert data["pages"][1]["path"] == "/Getting-Started"
    assert data["pages"][1]["depth"] == 1
    assert data["pages"][2]["path"] == "/Getting-Started/Installation"
    assert data["pages"][2]["depth"] == 2
    assert data["pages"][3]["path"] == "/API-Reference"
    assert data["pages"][3]["depth"] == 1


# --- wiki_get_page_content ---


@respx.mock
@pytest.mark.asyncio
async def test_wiki_get_page_content(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis/wiki-1/pages").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "/Getting-Started",
                "content": "# Getting Started\n\nWelcome to the wiki.",
                "gitItemPath": "/Getting-Started.md",
            },
            headers={"ETag": '"abc123"'},
        )
    )

    result = await mcp_server.call_tool(
        "wiki_get_page_content", {"project": "Cast", "wiki_id": "wiki-1", "path": "/Getting-Started"}
    )
    data = parse_tool_result(result)

    assert data["path"] == "/Getting-Started"
    assert "Getting Started" in data["content"]
    assert data["gitItemPath"] == "/Getting-Started.md"
    assert data["etag"] == '"abc123"'


@respx.mock
@pytest.mark.asyncio
async def test_wiki_get_page_content_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis/wiki-1/pages").mock(
        return_value=httpx.Response(404, text="Page not found")
    )

    result = await mcp_server.call_tool(
        "wiki_get_page_content", {"project": "Cast", "wiki_id": "wiki-1", "path": "/Nonexistent"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


# --- wiki_create_or_update_page ---


@respx.mock
@pytest.mark.asyncio
async def test_wiki_create_page(mcp_server: FastMCP) -> None:
    route = respx.put(f"{BASE}/Cast/_apis/wiki/wikis/wiki-1/pages").mock(
        return_value=httpx.Response(
            201,
            json={"path": "/New-Page", "gitItemPath": "/New-Page.md"},
            headers={"ETag": '"new-etag"'},
        )
    )

    result = await mcp_server.call_tool(
        "wiki_create_or_update_page",
        {"project": "Cast", "wiki_id": "wiki-1", "path": "/New-Page", "content": "# New Page\n\nHello!"},
    )
    data = parse_tool_result(result)

    assert data["path"] == "/New-Page"
    assert data["eTag"] == '"new-etag"'

    # Verify no If-Match header for create
    request = route.calls[0].request
    assert "If-Match" not in request.headers


@respx.mock
@pytest.mark.asyncio
async def test_wiki_update_page(mcp_server: FastMCP) -> None:
    route = respx.put(f"{BASE}/Cast/_apis/wiki/wikis/wiki-1/pages").mock(
        return_value=httpx.Response(
            200,
            json={"path": "/Existing-Page", "gitItemPath": "/Existing-Page.md"},
            headers={"ETag": '"updated-etag"'},
        )
    )

    result = await mcp_server.call_tool(
        "wiki_create_or_update_page",
        {
            "project": "Cast",
            "wiki_id": "wiki-1",
            "path": "/Existing-Page",
            "content": "# Updated Content",
            "etag": '"abc123"',
        },
    )
    data = parse_tool_result(result)

    assert data["path"] == "/Existing-Page"
    assert data["eTag"] == '"updated-etag"'

    # Verify If-Match header was sent
    request = route.calls[0].request
    assert request.headers["If-Match"] == '"abc123"'


@respx.mock
@pytest.mark.asyncio
async def test_wiki_update_page_conflict(mcp_server: FastMCP) -> None:
    respx.put(f"{BASE}/Cast/_apis/wiki/wikis/wiki-1/pages").mock(
        return_value=httpx.Response(409, text="Wiki page version conflict")
    )

    result = await mcp_server.call_tool(
        "wiki_create_or_update_page",
        {
            "project": "Cast",
            "wiki_id": "wiki-1",
            "path": "/Existing-Page",
            "content": "# Conflict",
            "etag": '"stale-etag"',
        },
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 409


# --- Error handling (decorator) ---


@respx.mock
@pytest.mark.asyncio
async def test_wiki_list_wikis_unauthorized(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    result = await mcp_server.call_tool("wiki_list_wikis", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 401


@respx.mock
@pytest.mark.asyncio
async def test_wiki_get_wiki_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis/nonexistent").mock(
        return_value=httpx.Response(404, text="Wiki not found")
    )

    result = await mcp_server.call_tool(
        "wiki_get_wiki", {"project": "Cast", "wiki_id": "nonexistent"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_wiki_list_pages_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/wiki/wikis/nonexistent/pages").mock(
        return_value=httpx.Response(404, text="Wiki not found")
    )

    result = await mcp_server.call_tool(
        "wiki_list_pages", {"project": "Cast", "wiki_id": "nonexistent"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404
