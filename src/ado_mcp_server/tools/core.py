"""Core ADO tools: projects, teams, identities."""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP

from ado_mcp_server.utils.ado_client import ADOClient
from ado_mcp_server.utils.auth import get_org, get_token
from ado_mcp_server.utils.tool_handler import handle_tool_errors


def register_core_tools(mcp: FastMCP) -> None:
    """Register all core tools with the MCP server."""

    @mcp.tool()
    @handle_tool_errors
    async def core_list_projects(
        ctx: Context,
        stateFilter: str = "wellFormed",
        top: int = 100,
        skip: int = 0,
        continuationToken: int | None = None,
        projectNameFilter: str | None = None,
    ) -> str:
        """Retrieve a list of projects in your Azure DevOps organization."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            params: dict = {"stateFilter": stateFilter, "$top": top, "$skip": skip}
            if continuationToken is not None:
                params["continuationToken"] = continuationToken
            if projectNameFilter:
                params["projectNameFilter"] = projectNameFilter
            result = await client.get("/_apis/projects", params=params)
            projects = [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "description": p.get("description", ""),
                    "state": p.get("state", ""),
                    "url": p.get("url", ""),
                }
                for p in result.get("value", [])
            ]
            return json.dumps({"count": len(projects), "projects": projects}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def core_list_project_teams(
        ctx: Context,
        project: str,
        mine: bool = False,
        top: int = 100,
        skip: int = 0,
    ) -> str:
        """Retrieve a list of teams for the specified Azure DevOps project."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            params: dict = {"$top": top, "$skip": skip}
            if mine:
                params["$mine"] = "true"
            result = await client.get(f"/_apis/projects/{project}/teams", params=params)
            teams = [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "url": t.get("url", ""),
                }
                for t in result.get("value", [])
            ]
            return json.dumps({"count": len(teams), "teams": teams}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def core_get_identity_ids(
        ctx: Context,
        searchFilter: str,
    ) -> str:
        """Retrieve Azure DevOps identity IDs for a provided search filter."""
        org = get_org(ctx)
        token = get_token(ctx)
        vssps_client = ADOClient(org, token)
        vssps_client.base_url = f"https://vssps.dev.azure.com/{org}"
        try:
            result = await vssps_client.get(
                "/_apis/identities",
                params={"searchFilter": "General", "filterValue": searchFilter, "queryMembership": "None"},
            )
            identities = [
                {
                    "id": i["id"],
                    "displayName": i.get("providerDisplayName", i.get("displayName", "")),
                    "uniqueName": i.get("properties", {}).get("Account", {}).get("$value", ""),
                }
                for i in result.get("value", [])
            ]
            return json.dumps({"count": len(identities), "identities": identities}, indent=2)
        finally:
            await vssps_client.close()
