"""Tests for work item write tools."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from ado_mcp_server.tools.work_items import register_work_item_tools


@pytest.fixture
def mcp_server() -> FastMCP:
    server = FastMCP("test-server", stateless_http=True, json_response=True)
    register_work_item_tools(server)
    return server


def parse_tool_result(result: tuple) -> dict:
    """Extract JSON from FastMCP call_tool result tuple."""
    contents, _meta = result
    return json.loads(contents[0].text)


def _make_work_item_response(id_: int, **field_overrides) -> dict:
    fields = {
        "System.Id": id_,
        "System.WorkItemType": "User Story",
        "System.Title": f"Work Item {id_}",
        "System.State": "New",
        "System.AssignedTo": {"displayName": "Sam Copsey", "uniqueName": "sam@example.com"},
        "Microsoft.VSTS.Scheduling.StoryPoints": 5,
        "Microsoft.VSTS.Scheduling.RemainingWork": None,
        "Microsoft.VSTS.Scheduling.CompletedWork": None,
        "Microsoft.VSTS.Scheduling.OriginalEstimate": None,
        "System.IterationPath": "Cast",
        "System.AreaPath": "Cast",
        "System.Parent": None,
        "System.Description": "",
        "System.CreatedDate": "2026-03-11T10:00:00Z",
        "System.ChangedDate": "2026-03-11T10:00:00Z",
    }
    fields.update(field_overrides)
    return {"id": id_, "fields": fields}


# --- wit_create_work_item ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_create_work_item(mcp_server: FastMCP) -> None:
    route = respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/$User Story").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(200, **{"System.Title": "New Story"}))
    )

    result = await mcp_server.call_tool(
        "wit_create_work_item",
        {"project": "Cast", "type_name": "User Story", "title": "New Story"},
    )
    data = parse_tool_result(result)

    assert data["id"] == 200
    assert data["title"] == "New Story"

    # Verify JSON Patch body
    request = route.calls[0].request
    assert request.headers["content-type"] == "application/json-patch+json"
    body = json.loads(request.content)
    assert body[0]["op"] == "add"
    assert body[0]["path"] == "/fields/System.Title"
    assert body[0]["value"] == "New Story"


@respx.mock
@pytest.mark.asyncio
async def test_wit_create_work_item_with_fields(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/$Task").mock(
        return_value=httpx.Response(
            200, json=_make_work_item_response(201, **{"System.WorkItemType": "Task", "System.Title": "Dev Task"})
        )
    )

    extra_fields = {
        "System.AssignedTo": "sam@example.com",
        "Microsoft.VSTS.Scheduling.RemainingWork": 8,
    }
    result = await mcp_server.call_tool(
        "wit_create_work_item",
        {"project": "Cast", "type_name": "Task", "title": "Dev Task", "fields": extra_fields},
    )
    data = parse_tool_result(result)

    assert data["id"] == 201


@respx.mock
@pytest.mark.asyncio
async def test_wit_create_work_item_with_parent(mcp_server: FastMCP) -> None:
    route = respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/$Task").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(202))
    )

    result = await mcp_server.call_tool(
        "wit_create_work_item",
        {"project": "Cast", "type_name": "Task", "title": "Child Task", "parent_id": 100},
    )
    data = parse_tool_result(result)
    assert data["id"] == 202

    body = json.loads(route.calls[0].request.content)
    relation_op = [op for op in body if op.get("path") == "/relations/-"]
    assert len(relation_op) == 1
    assert relation_op[0]["value"]["rel"] == "System.LinkTypes.Hierarchy-Reverse"
    assert "100" in relation_op[0]["value"]["url"]


@respx.mock
@pytest.mark.asyncio
async def test_wit_create_work_item_error_403(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/$Bug").mock(
        return_value=httpx.Response(403, text="Access denied")
    )

    result = await mcp_server.call_tool(
        "wit_create_work_item",
        {"project": "Cast", "type_name": "Bug", "title": "Forbidden Bug"},
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 403


# --- wit_update_work_item ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_update_work_item(mcp_server: FastMCP) -> None:
    assigned_to = {"displayName": "Sam Copsey", "uniqueName": "sam@example.com"}
    route = respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/150").mock(
        return_value=httpx.Response(
            200,
            json=_make_work_item_response(
                150, **{"System.State": "Active", "System.AssignedTo": assigned_to}
            ),
        )
    )

    fields = {"System.State": "Active", "System.AssignedTo": "sam@example.com"}
    result = await mcp_server.call_tool(
        "wit_update_work_item",
        {"project": "Cast", "work_item_id": 150, "fields": fields},
    )
    data = parse_tool_result(result)

    assert data["id"] == 150
    assert data["state"] == "Active"

    request = route.calls[0].request
    assert request.headers["content-type"] == "application/json-patch+json"
    body = json.loads(request.content)
    assert len(body) == 2
    assert body[0]["path"] == "/fields/System.State"


@respx.mock
@pytest.mark.asyncio
async def test_wit_update_work_item_not_found(mcp_server: FastMCP) -> None:
    respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/9999").mock(
        return_value=httpx.Response(404, text="Work item 9999 does not exist")
    )

    fields = {"System.Title": "Nope"}
    result = await mcp_server.call_tool(
        "wit_update_work_item",
        {"project": "Cast", "work_item_id": 9999, "fields": fields},
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


# --- wit_update_work_items_batch ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_update_work_items_batch(mcp_server: FastMCP) -> None:
    respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/1").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(1, **{"System.State": "Closed"}))
    )
    respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/2").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(2, **{"System.State": "Closed"}))
    )

    updates = [
        {"id": 1, "fields": {"System.State": "Closed"}},
        {"id": 2, "fields": {"System.State": "Closed"}},
    ]
    result = await mcp_server.call_tool(
        "wit_update_work_items_batch",
        {"project": "Cast", "updates": updates},
    )
    data = parse_tool_result(result)

    assert data["total"] == 2
    assert data["succeeded"] == 2
    assert data["failed"] == 0


@respx.mock
@pytest.mark.asyncio
async def test_wit_update_work_items_batch_partial_failure(mcp_server: FastMCP) -> None:
    respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/1").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(1))
    )
    respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/2").mock(
        return_value=httpx.Response(403, text="Access denied")
    )

    updates = [
        {"id": 1, "fields": {"System.State": "Active"}},
        {"id": 2, "fields": {"System.State": "Active"}},
    ]
    result = await mcp_server.call_tool(
        "wit_update_work_items_batch",
        {"project": "Cast", "updates": updates},
    )
    data = parse_tool_result(result)

    assert data["total"] == 2
    assert data["succeeded"] == 1
    assert data["failed"] == 1
    assert data["results"][0]["success"] is True
    assert data["results"][1]["success"] is False
    assert data["results"][1]["status"] == 403


# --- wit_add_work_item_comment ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_add_work_item_comment(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/150/comments").mock(
        return_value=httpx.Response(200, json={
            "id": 1001,
            "text": "Updated via MCP",
            "createdBy": {"displayName": "Sam Copsey"},
            "createdDate": "2026-03-11T12:00:00Z",
        })
    )

    result = await mcp_server.call_tool(
        "wit_add_work_item_comment",
        {"project": "Cast", "work_item_id": 150, "text": "Updated via MCP"},
    )
    data = parse_tool_result(result)

    assert data["id"] == 1001
    assert data["work_item_id"] == 150
    assert data["text"] == "Updated via MCP"
    assert data["created_by"] == "Sam Copsey"


# --- wit_add_child_work_items ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_add_child_work_items(mcp_server: FastMCP) -> None:
    route = respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/50").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(50))
    )

    result = await mcp_server.call_tool(
        "wit_add_child_work_items",
        {"project": "Cast", "parent_id": 50, "child_ids": "51,52,53"},
    )
    data = parse_tool_result(result)
    assert data["id"] == 50

    body = json.loads(route.calls[0].request.content)
    assert len(body) == 3
    assert all(op["value"]["rel"] == "System.LinkTypes.Hierarchy-Forward" for op in body)
    assert "51" in body[0]["value"]["url"]
    assert "52" in body[1]["value"]["url"]
    assert "53" in body[2]["value"]["url"]


# --- wit_work_items_link ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_work_items_link(mcp_server: FastMCP) -> None:
    route = respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/10").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(10))
    )

    result = await mcp_server.call_tool(
        "wit_work_items_link",
        {"project": "Cast", "work_item_id": 10, "target_id": 20, "link_type": "System.LinkTypes.Dependency-Forward"},
    )
    data = parse_tool_result(result)
    assert data["id"] == 10

    body = json.loads(route.calls[0].request.content)
    assert body[0]["value"]["rel"] == "System.LinkTypes.Dependency-Forward"
    assert "20" in body[0]["value"]["url"]


# --- wit_work_item_unlink ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_work_item_unlink(mcp_server: FastMCP) -> None:
    route = respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/10").mock(
        return_value=httpx.Response(200, json=_make_work_item_response(10))
    )

    result = await mcp_server.call_tool(
        "wit_work_item_unlink",
        {"project": "Cast", "work_item_id": 10, "relation_index": 2},
    )
    data = parse_tool_result(result)
    assert data["id"] == 10

    body = json.loads(route.calls[0].request.content)
    assert body[0]["op"] == "remove"
    assert body[0]["path"] == "/relations/2"
