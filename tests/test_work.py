"""Tests for Work domain tools."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from ado_mcp_server.tools.work import register_work_tools


@pytest.fixture
def mcp_server() -> FastMCP:
    server = FastMCP("test-server", stateless_http=True, json_response=True)
    register_work_tools(server)
    return server


def parse_tool_result(result: tuple) -> dict:
    """Extract JSON from FastMCP call_tool result tuple."""
    contents, _meta = result
    return json.loads(contents[0].text)


# --- work_list_iterations ---


@respx.mock
@pytest.mark.asyncio
async def test_work_list_iterations(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/_apis/wit/classificationnodes/Iterations").mock(
        return_value=httpx.Response(200, json={
            "id": 0,
            "name": "Cast",
            "path": "\\Cast\\Iteration",
            "children": [
                {
                    "id": 10,
                    "identifier": "iter-1",
                    "name": "Sprint 1",
                    "path": "\\Cast\\Iteration\\Sprint 1",
                    "hasChildren": False,
                    "attributes": {
                        "startDate": "2026-01-06T00:00:00Z",
                        "finishDate": "2026-01-17T00:00:00Z",
                    },
                },
                {
                    "id": 11,
                    "identifier": "iter-2",
                    "name": "Sprint 2",
                    "path": "\\Cast\\Iteration\\Sprint 2",
                    "hasChildren": False,
                    "attributes": {},
                },
            ],
        })
    )

    result = await mcp_server.call_tool("work_list_iterations", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["iterations"][0]["name"] == "Sprint 1"
    assert data["iterations"][0]["identifier"] == "iter-1"
    assert data["iterations"][1]["name"] == "Sprint 2"
    assert data["iterations"][1]["start_date"] is None


# --- work_create_iteration ---


@respx.mock
@pytest.mark.asyncio
async def test_work_create_iteration(mcp_server: FastMCP) -> None:
    route = respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/classificationnodes/Iterations").mock(
        return_value=httpx.Response(200, json={
            "id": 999,
            "identifier": "guid-123",
            "name": "Sprint 7",
            "path": "\\Cast\\Iteration\\Sprint 7",
            "attributes": {"startDate": "2026-03-30", "finishDate": "2026-04-10"},
        })
    )

    result = await mcp_server.call_tool(
        "work_create_iteration",
        {"project": "Cast", "name": "Sprint 7", "start_date": "2026-03-30", "end_date": "2026-04-10"},
    )
    data = parse_tool_result(result)

    assert data["name"] == "Sprint 7"
    assert data["id"] == 999

    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "Sprint 7"
    assert body["attributes"]["startDate"] == "2026-03-30T00:00:00Z"
    assert body["attributes"]["finishDate"] == "2026-04-10T00:00:00Z"


@respx.mock
@pytest.mark.asyncio
async def test_work_create_iteration_no_dates(mcp_server: FastMCP) -> None:
    route = respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/classificationnodes/Iterations").mock(
        return_value=httpx.Response(200, json={
            "id": 1000,
            "identifier": "guid-456",
            "name": "Backlog",
            "path": "\\Cast\\Iteration\\Backlog",
            "attributes": {},
        })
    )

    result = await mcp_server.call_tool(
        "work_create_iteration",
        {"project": "Cast", "name": "Backlog"},
    )
    data = parse_tool_result(result)

    assert data["name"] == "Backlog"

    body = json.loads(route.calls[0].request.content)
    assert body == {"name": "Backlog"}


# --- work_update_iteration ---


@respx.mock
@pytest.mark.asyncio
async def test_work_update_iteration(mcp_server: FastMCP) -> None:
    route = respx.patch(
        "https://dev.azure.com/test-org/Cast/_apis/wit/classificationnodes/Iterations/Sprint 2"
    ).mock(
        return_value=httpx.Response(200, json={
            "id": 11,
            "identifier": "guid-sprint2",
            "name": "Sprint 2",
            "path": "\\Cast\\Iteration\\Sprint 2",
            "attributes": {
                "startDate": "2026-05-20T00:00:00Z",
                "finishDate": "2026-05-31T00:00:00Z",
            },
        })
    )

    result = await mcp_server.call_tool(
        "work_update_iteration",
        {"project": "Cast", "iteration_name": "Sprint 2", "start_date": "2026-05-20", "end_date": "2026-05-31"},
    )
    data = parse_tool_result(result)

    assert data["name"] == "Sprint 2"
    assert data["start_date"] == "2026-05-20T00:00:00Z"
    assert data["end_date"] == "2026-05-31T00:00:00Z"

    body = json.loads(route.calls[0].request.content)
    assert body["attributes"]["startDate"] == "2026-05-20T00:00:00Z"
    assert body["attributes"]["finishDate"] == "2026-05-31T00:00:00Z"


@respx.mock
@pytest.mark.asyncio
async def test_work_update_iteration_error(mcp_server: FastMCP) -> None:
    respx.patch(
        "https://dev.azure.com/test-org/Cast/_apis/wit/classificationnodes/Iterations/Nonexistent"
    ).mock(
        return_value=httpx.Response(404, text="Iteration not found")
    )

    result = await mcp_server.call_tool(
        "work_update_iteration",
        {"project": "Cast", "iteration_name": "Nonexistent", "start_date": "2026-05-20"},
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


# --- work_assign_iteration_to_team ---


@respx.mock
@pytest.mark.asyncio
async def test_work_assign_iteration_to_team(mcp_server: FastMCP) -> None:
    route = respx.post("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations").mock(
        return_value=httpx.Response(200, json={
            "id": "guid-123",
            "name": "Sprint 7",
            "path": "Cast\\Sprint 7",
            "attributes": {"startDate": "2026-03-30T00:00:00Z", "finishDate": "2026-04-10T00:00:00Z"},
        })
    )

    result = await mcp_server.call_tool(
        "work_assign_iteration_to_team",
        {"project": "Cast", "team": "Cast Team", "iteration_id": "guid-123"},
    )
    data = parse_tool_result(result)

    assert data["id"] == "guid-123"
    assert data["name"] == "Sprint 7"

    body = json.loads(route.calls[0].request.content)
    assert body == {"id": "guid-123"}


# --- work_update_team_capacity ---


@respx.mock
@pytest.mark.asyncio
async def test_work_update_team_capacity(mcp_server: FastMCP) -> None:
    route = respx.patch(
        "https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations/iter-3/capacities/member-1"
    ).mock(
        return_value=httpx.Response(200, json={
            "teamMember": {"displayName": "Sam Copsey"},
            "activities": [{"name": "Development", "capacityPerDay": 6}],
            "daysOff": [{"start": "2026-03-10", "end": "2026-03-10"}],
        })
    )

    activities = [{"name": "Development", "capacityPerDay": 6}]
    days_off = [{"start": "2026-03-10", "end": "2026-03-10"}]
    result = await mcp_server.call_tool(
        "work_update_team_capacity",
        {
            "project": "Cast",
            "team": "Cast Team",
            "iteration_id": "iter-3",
            "member_id": "member-1",
            "activities": activities,
            "days_off": days_off,
        },
    )
    data = parse_tool_result(result)

    assert data["team_member"] == "Sam Copsey"
    assert data["activities"][0]["capacityPerDay"] == 6

    body = json.loads(route.calls[0].request.content)
    assert body["activities"][0]["name"] == "Development"
    assert len(body["daysOff"]) == 1


@respx.mock
@pytest.mark.asyncio
async def test_work_update_team_capacity_error(mcp_server: FastMCP) -> None:
    respx.patch(
        "https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings/iterations/iter-3/capacities/member-1"
    ).mock(
        return_value=httpx.Response(403, text="Access denied")
    )

    activities = [{"name": "Development", "capacityPerDay": 6}]
    result = await mcp_server.call_tool(
        "work_update_team_capacity",
        {
            "project": "Cast",
            "team": "Cast Team",
            "iteration_id": "iter-3",
            "member_id": "member-1",
            "activities": activities,
        },
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 403


# --- work_get_team_settings ---


@respx.mock
@pytest.mark.asyncio
async def test_work_get_team_settings(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings").mock(
        return_value=httpx.Response(200, json={
            "defaultIteration": {"id": "iter-3", "name": "Sprint 3", "path": "Cast\\Sprint 3"},
            "backlogIteration": {"id": "iter-root", "name": "Cast", "path": "Cast"},
            "workingDays": ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "bugsBehavior": "asTasks",
            "backlogVisibilities": {
                "Microsoft.EpicCategory": True,
                "Microsoft.FeatureCategory": True,
                "Microsoft.RequirementCategory": True,
            },
        })
    )

    result = await mcp_server.call_tool(
        "work_get_team_settings",
        {"project": "Cast", "team": "Cast Team"},
    )
    data = parse_tool_result(result)

    assert data["default_iteration"]["name"] == "Sprint 3"
    assert data["backlog_iteration"]["name"] == "Cast"
    assert len(data["working_days"]) == 5
    assert data["bugs_behavior"] == "asTasks"


# --- Error handling (decorator) ---


@respx.mock
@pytest.mark.asyncio
async def test_work_list_iterations_not_found(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Nonexistent/_apis/wit/classificationnodes/Iterations").mock(
        return_value=httpx.Response(404, text="Project not found")
    )

    result = await mcp_server.call_tool("work_list_iterations", {"project": "Nonexistent"})
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_work_get_team_settings_unauthorized(mcp_server: FastMCP) -> None:
    respx.get("https://dev.azure.com/test-org/Cast/Cast Team/_apis/work/teamsettings").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    result = await mcp_server.call_tool(
        "work_get_team_settings", {"project": "Cast", "team": "Cast Team"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 401
