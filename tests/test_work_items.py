"""Tests for work item read tools."""

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


# --- Helpers for mock responses ---


def _wiql_response(ids: list[int]) -> dict:
    return {"workItems": [{"id": i, "url": f"https://dev.azure.com/test-org/_apis/wit/workItems/{i}"} for i in ids]}


def _work_items_response(items: list[dict]) -> dict:
    return {"count": len(items), "value": items}


def _make_work_item(id_: int, **overrides) -> dict:
    fields = {
        "System.Id": id_,
        "System.WorkItemType": "User Story",
        "System.Title": f"Work Item {id_}",
        "System.State": "Active",
        "System.AssignedTo": {"displayName": "Sam Copsey", "uniqueName": "sam@example.com"},
        "Microsoft.VSTS.Scheduling.StoryPoints": 5,
        "Microsoft.VSTS.Scheduling.RemainingWork": 3.0,
        "Microsoft.VSTS.Scheduling.CompletedWork": 2.0,
        "Microsoft.VSTS.Scheduling.OriginalEstimate": 8.0,
        "System.IterationPath": "Cast\\Sprint 3",
        "System.AreaPath": "Cast",
        "System.Parent": 42,
        "System.Description": "Description text",
        "System.CreatedDate": "2026-01-15T10:00:00Z",
        "System.ChangedDate": "2026-03-10T14:30:00Z",
    }
    fields.update(overrides)
    return {"id": id_, "fields": fields}


# --- wit_query_wiql ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_query_wiql(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/wiql").mock(
        return_value=httpx.Response(200, json=_wiql_response([1, 2, 3]))
    )

    result = await mcp_server.call_tool(
        "wit_query_wiql",
        {
            "project": "Cast",
            "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'",
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 3
    assert data["work_items"][0]["id"] == 1
    assert "title" not in data["work_items"][0]  # No details by default


@respx.mock
@pytest.mark.asyncio
async def test_wit_query_wiql_with_details(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/wiql").mock(
        return_value=httpx.Response(200, json=_wiql_response([10, 20]))
    )
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems").mock(
        return_value=httpx.Response(
            200,
            json=_work_items_response(
                [
                    _make_work_item(10, **{"System.Title": "Story A"}),
                    _make_work_item(20, **{"System.Title": "Story B"}),
                ]
            ),
        )
    )

    result = await mcp_server.call_tool(
        "wit_query_wiql",
        {
            "project": "Cast",
            "wiql": "SELECT [System.Id] FROM WorkItems",
            "fetch_details": True,
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["work_items"][0]["title"] == "Story A"
    assert data["work_items"][1]["title"] == "Story B"
    assert data["work_items"][0]["assigned_to"] == "Sam Copsey"


@respx.mock
@pytest.mark.asyncio
async def test_wit_query_wiql_with_custom_fields(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/wiql").mock(
        return_value=httpx.Response(200, json=_wiql_response([5]))
    )
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems").mock(
        return_value=httpx.Response(
            200,
            json=_work_items_response(
                [
                    {"id": 5, "fields": {"System.Id": 5, "System.Title": "Custom", "Custom.TShirtSize": "Large"}},
                ]
            ),
        )
    )

    result = await mcp_server.call_tool(
        "wit_query_wiql",
        {
            "project": "Cast",
            "wiql": "SELECT [System.Id] FROM WorkItems",
            "fetch_details": True,
            "fields": "System.Id,System.Title,Custom.TShirtSize",
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["work_items"][0]["title"] == "Custom"
    assert data["work_items"][0]["Custom.TShirtSize"] == "Large"


@respx.mock
@pytest.mark.asyncio
async def test_wit_query_wiql_empty_result(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/wiql").mock(
        return_value=httpx.Response(200, json={"workItems": []})
    )

    result = await mcp_server.call_tool(
        "wit_query_wiql",
        {
            "project": "Cast",
            "wiql": "SELECT [System.Id] FROM WorkItems WHERE 1=0",
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 0
    assert data["work_items"] == []


# --- wit_get_work_item ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_item(mcp_server: FastMCP) -> None:
    item = _make_work_item(42)
    item["relations"] = [
        {
            "rel": "System.LinkTypes.Hierarchy-Reverse",
            "url": "https://dev.azure.com/test-org/_apis/wit/workItems/10",
            "attributes": {"name": "Parent"},
        },
    ]
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/42").mock(
        return_value=httpx.Response(200, json=item)
    )

    result = await mcp_server.call_tool("wit_get_work_item", {"project": "Cast", "work_item_id": 42})
    data = parse_tool_result(result)

    assert data["id"] == 42
    assert data["title"] == "Work Item 42"
    assert data["story_points"] == 5
    assert data["completed_work"] == 2.0
    assert data["original_estimate"] == 8.0
    assert len(data["relations"]) == 1
    assert data["relations"][0]["type"] == "Parent"
    assert data["relations"][0]["target_id"] == 10
    assert data["relations"][0]["rel"] == "System.LinkTypes.Hierarchy-Reverse"


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_item_with_custom_fields(mcp_server: FastMCP) -> None:
    item = {"id": 7, "fields": {"System.Id": 7, "Custom.Priority": "P1", "Custom.Team": "Platform"}}
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/7").mock(
        return_value=httpx.Response(200, json=item)
    )

    result = await mcp_server.call_tool(
        "wit_get_work_item",
        {
            "project": "Cast",
            "work_item_id": 7,
            "fields": "System.Id,Custom.Priority,Custom.Team",
        },
    )
    data = parse_tool_result(result)

    assert data["id"] == 7
    assert data["Custom.Priority"] == "P1"
    assert data["Custom.Team"] == "Platform"


# --- wit_get_work_items_batch ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_items_batch(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems").mock(
        return_value=httpx.Response(
            200,
            json=_work_items_response(
                [
                    _make_work_item(1),
                    _make_work_item(2),
                    _make_work_item(3),
                ]
            ),
        )
    )

    result = await mcp_server.call_tool(
        "wit_get_work_items_batch",
        {
            "project": "Cast",
            "ids": "1,2,3",
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 3
    assert data["work_items"][0]["id"] == 1
    assert data["work_items"][2]["remaining_work"] == 3.0


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_items_batch_large(mcp_server: FastMCP) -> None:
    """Over 200 IDs should trigger multiple batch requests."""
    all_ids = list(range(1, 251))  # 250 IDs

    # First batch (1-200)
    batch1_items = [_make_work_item(i) for i in range(1, 201)]
    # Second batch (201-250)
    batch2_items = [_make_work_item(i) for i in range(201, 251)]

    call_count = 0

    def mock_batch(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        ids_param = request.url.params.get("ids", "")
        id_list = [int(x) for x in ids_param.split(",")]
        if id_list[0] == 1:
            return httpx.Response(200, json=_work_items_response(batch1_items))
        return httpx.Response(200, json=_work_items_response(batch2_items))

    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems").mock(side_effect=mock_batch)

    result = await mcp_server.call_tool(
        "wit_get_work_items_batch",
        {
            "project": "Cast",
            "ids": ",".join(str(i) for i in all_ids),
        },
    )
    data = parse_tool_result(result)

    assert call_count == 2
    assert data["count"] == 250


# --- wit_get_work_items_for_iteration ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_items_for_iteration(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/wiql").mock(
        return_value=httpx.Response(200, json=_wiql_response([100, 101]))
    )
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems").mock(
        return_value=httpx.Response(
            200,
            json=_work_items_response(
                [
                    _make_work_item(100, **{"System.IterationPath": "Cast\\Sprint 3"}),
                    _make_work_item(101, **{"System.IterationPath": "Cast\\Sprint 3"}),
                ]
            ),
        )
    )

    result = await mcp_server.call_tool(
        "wit_get_work_items_for_iteration",
        {
            "project": "Cast",
            "iteration_path": "Cast\\Sprint 3",
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["work_items"][0]["iteration_path"] == "Cast\\Sprint 3"


# --- wit_my_work_items ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_my_work_items(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/_apis/wit/wiql").mock(
        return_value=httpx.Response(200, json=_wiql_response([50, 51]))
    )
    respx.get("https://dev.azure.com/test-org/_apis/wit/workitems").mock(
        return_value=httpx.Response(
            200,
            json=_work_items_response(
                [
                    _make_work_item(50, **{"System.AssignedTo": {"displayName": "Me", "uniqueName": "me@example.com"}}),
                    _make_work_item(51, **{"System.AssignedTo": {"displayName": "Me", "uniqueName": "me@example.com"}}),
                ]
            ),
        )
    )

    result = await mcp_server.call_tool("wit_my_work_items", {})
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["work_items"][0]["assigned_to"] == "Me"


# --- wit_list_backlogs ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_list_backlogs(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/backlogs").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"id": "Microsoft.EpicCategory", "name": "Epics", "rank": 1, "workItemTypes": [{"name": "Epic"}]},
                    {
                        "id": "Microsoft.FeatureCategory",
                        "name": "Features",
                        "rank": 2,
                        "workItemTypes": [{"name": "Feature"}],
                    },
                    {
                        "id": "Microsoft.RequirementCategory",
                        "name": "Stories",
                        "rank": 3,
                        "workItemTypes": [{"name": "User Story"}],
                    },
                ],
            },
        )
    )

    result = await mcp_server.call_tool("wit_list_backlogs", {"project": "Cast", "team": "Cast Team"})
    data = parse_tool_result(result)

    assert data["count"] == 3
    assert data["backlogs"][0]["name"] == "Epics"
    assert data["backlogs"][2]["work_item_types"] == ["User Story"]


# --- wit_list_backlog_work_items ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_list_backlog_work_items(mcp_server: FastMCP) -> None:
    respx.get(
        "https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/backlogs/Microsoft.RequirementCategory/workItems"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "workItems": [
                    {"target": {"id": 10, "url": "https://dev.azure.com/test-org/_apis/wit/workItems/10"}},
                    {"target": {"id": 11, "url": "https://dev.azure.com/test-org/_apis/wit/workItems/11"}},
                ],
            },
        )
    )

    result = await mcp_server.call_tool(
        "wit_list_backlog_work_items",
        {
            "project": "Cast",
            "team": "Cast Team",
            "backlog_id": "Microsoft.RequirementCategory",
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["work_items"][0]["target"]["id"] == 10


# --- wit_get_work_item_type ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_item_type(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitemtypes/User Story").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "User Story",
                "referenceName": "Microsoft.VSTS.WorkItemTypes.UserStory",
                "description": "Tracks user-facing value",
                "fieldInstances": [
                    {
                        "referenceName": "System.Title",
                        "name": "Title",
                        "type": "string",
                        "alwaysRequired": True,
                        "defaultValue": "",
                    },
                    {
                        "referenceName": "Microsoft.VSTS.Scheduling.StoryPoints",
                        "name": "Story Points",
                        "type": "double",
                        "alwaysRequired": False,
                        "defaultValue": "",
                    },
                ],
                "states": [
                    {"name": "New", "color": "b2b2b2", "category": "Proposed"},
                    {"name": "Active", "color": "007acc", "category": "InProgress"},
                    {"name": "Closed", "color": "339933", "category": "Completed"},
                ],
                "transitions": {
                    "New": [{"to": "Active"}, {"to": "Closed"}],
                    "Active": [{"to": "New"}, {"to": "Closed"}],
                },
            },
        )
    )

    result = await mcp_server.call_tool("wit_get_work_item_type", {"project": "Cast", "type_name": "User Story"})
    data = parse_tool_result(result)

    assert data["name"] == "User Story"
    assert len(data["fields"]) == 2
    assert data["fields"][0]["reference_name"] == "System.Title"
    assert data["fields"][0]["always_required"] is True
    assert len(data["states"]) == 3
    assert data["states"][1]["name"] == "Active"


# --- wit_list_fields ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_list_fields(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/fields").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 3,
                "value": [
                    {
                        "referenceName": "System.Id",
                        "name": "ID",
                        "type": "integer",
                        "isIdentity": False,
                        "isPicklist": False,
                        "usage": "workItem",
                    },
                    {
                        "referenceName": "System.Title",
                        "name": "Title",
                        "type": "string",
                        "isIdentity": False,
                        "isPicklist": False,
                        "usage": "workItem",
                    },
                    {
                        "referenceName": "Custom.TShirtSize",
                        "name": "T-Shirt Size",
                        "type": "string",
                        "isIdentity": False,
                        "isPicklist": True,
                        "usage": "workItem",
                    },
                ],
            },
        )
    )

    result = await mcp_server.call_tool("wit_list_fields", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 3
    assert data["fields"][0]["reference_name"] == "System.Id"
    assert data["fields"][2]["reference_name"] == "Custom.TShirtSize"
    assert data["fields"][2]["is_picklist"] is True


@respx.mock
@pytest.mark.asyncio
async def test_wit_list_fields_includes_custom(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/fields").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 4,
                "value": [
                    {
                        "referenceName": "System.Id",
                        "name": "ID",
                        "type": "integer",
                        "isIdentity": False,
                        "isPicklist": False,
                        "usage": "workItem",
                    },
                    {
                        "referenceName": "Custom.TShirtSize",
                        "name": "T-Shirt Size",
                        "type": "string",
                        "isIdentity": False,
                        "isPicklist": True,
                        "usage": "workItem",
                    },
                    {
                        "referenceName": "Custom.BusinessValue",
                        "name": "Business Value",
                        "type": "integer",
                        "isIdentity": False,
                        "isPicklist": False,
                        "usage": "workItem",
                    },
                    {
                        "referenceName": "Custom.Team",
                        "name": "Team",
                        "type": "string",
                        "isIdentity": False,
                        "isPicklist": True,
                        "usage": "workItem",
                    },
                ],
            },
        )
    )

    result = await mcp_server.call_tool("wit_list_fields", {"project": "Cast"})
    data = parse_tool_result(result)

    custom_fields = [f for f in data["fields"] if f["reference_name"].startswith("Custom.")]
    assert len(custom_fields) == 3
    assert custom_fields[0]["reference_name"] == "Custom.TShirtSize"


# --- wit_get_team_iterations ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_team_iterations(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 3,
                "value": [
                    {
                        "id": "iter-1",
                        "name": "Sprint 1",
                        "path": "Cast\\Sprint 1",
                        "attributes": {
                            "startDate": "2026-01-06T00:00:00Z",
                            "finishDate": "2026-01-17T00:00:00Z",
                            "timeFrame": "past",
                        },
                    },
                    {
                        "id": "iter-2",
                        "name": "Sprint 2",
                        "path": "Cast\\Sprint 2",
                        "attributes": {
                            "startDate": "2026-01-20T00:00:00Z",
                            "finishDate": "2026-01-31T00:00:00Z",
                            "timeFrame": "past",
                        },
                    },
                    {
                        "id": "iter-3",
                        "name": "Sprint 3",
                        "path": "Cast\\Sprint 3",
                        "attributes": {
                            "startDate": "2026-03-03T00:00:00Z",
                            "finishDate": "2026-03-14T00:00:00Z",
                            "timeFrame": "current",
                        },
                    },
                ],
            },
        )
    )

    result = await mcp_server.call_tool("wit_get_team_iterations", {"project": "Cast", "team": "Cast Team"})
    data = parse_tool_result(result)

    assert data["count"] == 3
    assert data["iterations"][0]["name"] == "Sprint 1"
    assert data["iterations"][2]["time_frame"] == "current"


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_team_iterations_current(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "value": [
                    {
                        "id": "iter-3",
                        "name": "Sprint 3",
                        "path": "Cast\\Sprint 3",
                        "attributes": {
                            "startDate": "2026-03-03T00:00:00Z",
                            "finishDate": "2026-03-14T00:00:00Z",
                            "timeFrame": "current",
                        },
                    },
                ],
            },
        )
    )

    result = await mcp_server.call_tool(
        "wit_get_team_iterations",
        {
            "project": "Cast",
            "team": "Cast Team",
            "timeframe": "current",
        },
    )
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["iterations"][0]["time_frame"] == "current"


# --- wit_get_team_capacity ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_team_capacity(mcp_server: FastMCP) -> None:
    # Sprint 3: 2026-03-02 (Mon) to 2026-03-13 (Fri) = 10 working days
    respx.get(
        "https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations/iter-3/capacities"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "teamMember": {"displayName": "Sam Copsey"},
                        "activities": [{"name": "Development", "capacityPerDay": 6}],
                        "daysOff": [],
                    },
                    {
                        "teamMember": {"displayName": "Jane Dev"},
                        "activities": [
                            {"name": "Development", "capacityPerDay": 7},
                            {"name": "Testing", "capacityPerDay": 1},
                        ],
                        "daysOff": [],
                    },
                ],
            },
        )
    )
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations/iter-3").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "iter-3",
                "attributes": {
                    "startDate": "2026-03-02T00:00:00Z",
                    "finishDate": "2026-03-13T00:00:00Z",
                },
            },
        )
    )

    result = await mcp_server.call_tool(
        "wit_get_team_capacity",
        {
            "project": "Cast",
            "team": "Cast Team",
            "iteration_id": "iter-3",
        },
    )
    data = parse_tool_result(result)

    assert data["working_days"] == 10
    assert data["count"] == 3  # Sam:Dev, Jane:Dev, Jane:Testing
    assert data["capacities"][0]["team_member"] == "Sam Copsey"
    assert data["capacities"][0]["total_available_hours"] == 60  # 6 * 10
    assert data["capacities"][1]["total_available_hours"] == 70  # 7 * 10
    assert data["capacities"][2]["total_available_hours"] == 10  # 1 * 10


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_team_capacity_with_days_off(mcp_server: FastMCP) -> None:
    # Sprint 3: 2026-03-02 (Mon) to 2026-03-13 (Fri) = 10 working days, Sam has 2 days off
    respx.get(
        "https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations/iter-3/capacities"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "teamMember": {"displayName": "Sam Copsey"},
                        "activities": [{"name": "Development", "capacityPerDay": 6}],
                        "daysOff": [
                            {"start": "2026-03-10T00:00:00Z", "end": "2026-03-10T00:00:00Z"},
                            {"start": "2026-03-11T00:00:00Z", "end": "2026-03-11T00:00:00Z"},
                        ],
                    },
                ],
            },
        )
    )
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations/iter-3").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "iter-3",
                "attributes": {
                    "startDate": "2026-03-02T00:00:00Z",
                    "finishDate": "2026-03-13T00:00:00Z",
                },
            },
        )
    )

    result = await mcp_server.call_tool(
        "wit_get_team_capacity",
        {
            "project": "Cast",
            "team": "Cast Team",
            "iteration_id": "iter-3",
        },
    )
    data = parse_tool_result(result)

    assert data["capacities"][0]["days_off"] == 2
    assert data["capacities"][0]["effective_days"] == 8  # 10 - 2
    assert data["capacities"][0]["total_available_hours"] == 48  # 6 * 8


# --- Error handling (decorator) ---


@respx.mock
@pytest.mark.asyncio
async def test_wit_query_wiql_error(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/wiql").mock(
        return_value=httpx.Response(400, text="Invalid WIQL syntax")
    )

    result = await mcp_server.call_tool(
        "wit_query_wiql",
        {"project": "Cast", "wiql": "INVALID QUERY"},
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 400


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_item_not_found(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/9999").mock(
        return_value=httpx.Response(404, text="Work item 9999 does not exist")
    )

    result = await mcp_server.call_tool(
        "wit_get_work_item", {"project": "Cast", "work_item_id": 9999}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_items_batch_unauthorized(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitems").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    result = await mcp_server.call_tool(
        "wit_get_work_items_batch", {"project": "Cast", "ids": "1,2,3"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 401


@respx.mock
@pytest.mark.asyncio
async def test_wit_list_backlogs_forbidden(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/backlogs").mock(
        return_value=httpx.Response(403, text="Access denied")
    )

    result = await mcp_server.call_tool(
        "wit_list_backlogs", {"project": "Cast", "team": "Cast Team"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 403


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_work_item_type_not_found(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/workitemtypes/Nonexistent").mock(
        return_value=httpx.Response(404, text="Type not found")
    )

    result = await mcp_server.call_tool(
        "wit_get_work_item_type", {"project": "Cast", "type_name": "Nonexistent"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_wit_my_work_items_unauthorized(mcp_server: FastMCP) -> None:
    respx.post("https://dev.azure.com/test-org/_apis/wit/wiql").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    result = await mcp_server.call_tool("wit_my_work_items", {})
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 401


@respx.mock
@pytest.mark.asyncio
async def test_wit_get_team_iterations_not_found(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/Nonexistent/_apis/work/teamsettings/iterations").mock(
        return_value=httpx.Response(404, text="Team not found")
    )

    result = await mcp_server.call_tool(
        "wit_get_team_iterations", {"project": "Cast", "team": "Nonexistent"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404
