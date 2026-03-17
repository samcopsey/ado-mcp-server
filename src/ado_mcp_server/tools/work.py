"""Work domain tools: iterations, team settings, capacity management."""

from __future__ import annotations

import json
import re

from mcp.server.fastmcp import Context, FastMCP

from ado_mcp_server.utils.ado_client import ADOClient
from ado_mcp_server.utils.auth import get_org, get_token
from ado_mcp_server.utils.tool_handler import handle_tool_errors


def _normalize_date(date_str: str) -> str:
    """Ensure a date string is in full ISO 8601 UTC format for ADO."""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return f"{date_str}T00:00:00Z"
    return date_str


def register_work_tools(mcp: FastMCP) -> None:
    """Register Work domain tools with the MCP server."""

    @mcp.tool()
    @handle_tool_errors
    async def work_list_iterations(
        ctx: Context,
        project: str,
        depth: int = 2,
    ) -> str:
        """List all iterations defined in a project (project-level classification nodes).

        Returns the full iteration tree including iterations not yet assigned to any team.
        Use wit_get_team_iterations to see which iterations are assigned to a specific team.

        Args:
            project: ADO project name.
            depth: How many levels deep to traverse the tree (default 2).
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/_apis/wit/classificationnodes/Iterations",
                params={"$depth": str(depth)},
            )

            def _flatten(node: dict, iterations: list) -> None:
                attrs = node.get("attributes", {})
                iterations.append({
                    "id": node.get("id", ""),
                    "identifier": node.get("identifier", ""),
                    "name": node.get("name", ""),
                    "path": node.get("path", ""),
                    "has_children": node.get("hasChildren", False),
                    "start_date": attrs.get("startDate"),
                    "end_date": attrs.get("finishDate"),
                })
                for child in node.get("children", []):
                    _flatten(child, iterations)

            iterations: list[dict] = []
            # The root node is the project itself; list its children
            for child in result.get("children", []):
                _flatten(child, iterations)

            return json.dumps({"count": len(iterations), "iterations": iterations}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def work_create_iteration(
        ctx: Context,
        project: str,
        name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Create a new iteration (sprint) in a project.

        Args:
            project: ADO project name.
            name: Iteration name (e.g. "Sprint 7").
            start_date: Optional start date in ISO format (e.g. "2026-03-30").
            end_date: Optional end date in ISO format (e.g. "2026-04-10").
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            body: dict = {"name": name}
            if start_date or end_date:
                body["attributes"] = {}
                if start_date:
                    body["attributes"]["startDate"] = _normalize_date(start_date)
                if end_date:
                    body["attributes"]["finishDate"] = _normalize_date(end_date)

            result = await client.post(
                f"/{project}/_apis/wit/classificationnodes/Iterations",
                json=body,
            )
            return json.dumps({
                "id": result.get("id"),
                "identifier": result.get("identifier", ""),
                "name": result.get("name", ""),
                "path": result.get("path", ""),
                "attributes": result.get("attributes", {}),
            }, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def work_update_iteration(
        ctx: Context,
        project: str,
        iteration_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Update an existing iteration's dates.

        Use this when an iteration already exists at the project level but needs
        its start/end dates set or changed. The iteration is identified by name
        (path under the root Iterations node).

        Args:
            project: ADO project name.
            iteration_name: Iteration name (e.g. "Sprint 2").
            start_date: New start date in ISO format (e.g. "2026-03-30").
            end_date: New end date in ISO format (e.g. "2026-04-10").
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            body: dict = {}
            if start_date or end_date:
                body["attributes"] = {}
                if start_date:
                    body["attributes"]["startDate"] = _normalize_date(start_date)
                if end_date:
                    body["attributes"]["finishDate"] = _normalize_date(end_date)

            result = await client.patch_json(
                f"/{project}/_apis/wit/classificationnodes/Iterations/{iteration_name}",
                json=body,
            )
            attrs = result.get("attributes", {})
            return json.dumps({
                "id": result.get("id"),
                "identifier": result.get("identifier", ""),
                "name": result.get("name", ""),
                "path": result.get("path", ""),
                "start_date": attrs.get("startDate"),
                "end_date": attrs.get("finishDate"),
            }, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def work_assign_iteration_to_team(
        ctx: Context,
        project: str,
        team: str,
        iteration_id: str,
    ) -> str:
        """Assign an existing iteration to a team's sprint schedule.

        Args:
            project: ADO project name.
            team: Team name.
            iteration_id: The iteration GUID to assign.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.post(
                f"/{project}/{team}/_apis/work/teamsettings/iterations",
                json={"id": iteration_id},
            )
            attrs = result.get("attributes", {})
            return json.dumps({
                "id": result.get("id", ""),
                "name": result.get("name", ""),
                "path": result.get("path", ""),
                "start_date": attrs.get("startDate"),
                "end_date": attrs.get("finishDate"),
            }, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def work_update_team_capacity(
        ctx: Context,
        project: str,
        team: str,
        iteration_id: str,
        member_id: str,
        activities: list,
        days_off: list | None = None,
    ) -> str:
        """Update a team member's capacity for a sprint iteration.

        Args:
            project: ADO project name.
            team: Team name.
            iteration_id: Sprint iteration GUID.
            member_id: Team member identity GUID.
            activities: List of activity objects,
                e.g. [{"name": "Development", "capacityPerDay": 6}].
            days_off: Optional list of day-off ranges,
                e.g. [{"start": "2026-03-10", "end": "2026-03-10"}].
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            body: dict = {"activities": activities}
            if days_off:
                body["daysOff"] = days_off
            else:
                body["daysOff"] = []

            result = await client.patch_json(
                f"/{project}/{team}/_apis/work/teamsettings/iterations/{iteration_id}/capacities/{member_id}",
                json=body,
            )
            return json.dumps({
                "team_member": result.get("teamMember", {}).get("displayName", ""),
                "activities": result.get("activities", []),
                "days_off": result.get("daysOff", []),
            }, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def work_get_team_settings(
        ctx: Context,
        project: str,
        team: str,
    ) -> str:
        """Get team settings including default iteration, backlog iteration, and working days.

        Args:
            project: ADO project name.
            team: Team name.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/{team}/_apis/work/teamsettings",
            )
            default_iter = result.get("defaultIteration", {})
            backlog_iter = result.get("backlogIteration", {})
            return json.dumps({
                "default_iteration": {
                    "id": default_iter.get("id", ""),
                    "name": default_iter.get("name", ""),
                    "path": default_iter.get("path", ""),
                },
                "backlog_iteration": {
                    "id": backlog_iter.get("id", ""),
                    "name": backlog_iter.get("name", ""),
                    "path": backlog_iter.get("path", ""),
                },
                "working_days": result.get("workingDays", []),
                "bugs_behavior": result.get("bugsBehavior", ""),
                "backlog_visibilities": result.get("backlogVisibilities", {}),
            }, indent=2)
        finally:
            await client.close()
