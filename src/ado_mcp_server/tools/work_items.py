"""Work item tools: WIQL queries, work items, backlogs, iterations, capacity, and writes."""

from __future__ import annotations

import datetime
import json

import httpx
from mcp.server.fastmcp import Context, FastMCP

from ado_mcp_server.utils.ado_client import ADOClient
from ado_mcp_server.utils.auth import get_org, get_token
from ado_mcp_server.utils.tool_handler import handle_tool_errors


def _build_field_ops(fields: dict) -> list[dict]:
    """Build JSON Patch operations from a field name->value dict."""
    return [
        {"op": "add", "path": f"/fields/{ref_name}", "value": value}
        for ref_name, value in fields.items()
    ]

_DEFAULT_FIELDS = [
    "System.Id",
    "System.WorkItemType",
    "System.Title",
    "System.State",
    "System.AssignedTo",
    "Microsoft.VSTS.Scheduling.StoryPoints",
    "Microsoft.VSTS.Scheduling.RemainingWork",
    "Microsoft.VSTS.Scheduling.CompletedWork",
    "Microsoft.VSTS.Scheduling.OriginalEstimate",
    "System.IterationPath",
    "System.AreaPath",
    "System.Parent",
    "System.Description",
    "System.CreatedDate",
    "System.ChangedDate",
]

# Maps default ADO field reference names to shorter output keys.
_FIELD_KEY_MAP = {
    "System.Id": "id",
    "System.WorkItemType": "type",
    "System.Title": "title",
    "System.State": "state",
    "System.AssignedTo": "assigned_to",
    "Microsoft.VSTS.Scheduling.StoryPoints": "story_points",
    "Microsoft.VSTS.Scheduling.RemainingWork": "remaining_work",
    "Microsoft.VSTS.Scheduling.CompletedWork": "completed_work",
    "Microsoft.VSTS.Scheduling.OriginalEstimate": "original_estimate",
    "System.IterationPath": "iteration_path",
    "System.AreaPath": "area_path",
    "System.Parent": "parent_id",
    "System.Description": "description",
    "System.CreatedDate": "created_date",
    "System.ChangedDate": "changed_date",
}


def _normalize_work_item(item: dict, fields: list[str] | None) -> dict:
    """Convert raw ADO work item response to a normalized dict.

    When default fields are used, keys are mapped to short names (e.g. "title").
    When custom fields are requested, reference names are preserved as-is for
    any field not in the default mapping.
    """
    f = item.get("fields", {})
    result: dict = {}

    target_fields = fields if fields is not None else _DEFAULT_FIELDS

    for ref_name in target_fields:
        value = f.get(ref_name)
        key = _FIELD_KEY_MAP.get(ref_name, ref_name)

        # AssignedTo comes back as a dict with displayName
        if ref_name == "System.AssignedTo" and isinstance(value, dict):
            value = value.get("displayName", "")

        result[key] = value

    # Always include id from the top level (create responses may not have System.Id in fields)
    if not result.get("id"):
        result["id"] = item.get("id")

    return result


# Map ADO relation type refs to human-readable names
_RELATION_TYPE_MAP = {
    "System.LinkTypes.Hierarchy-Forward": "Child",
    "System.LinkTypes.Hierarchy-Reverse": "Parent",
    "System.LinkTypes.Related": "Related",
    "System.LinkTypes.Dependency-Forward": "Successor",
    "System.LinkTypes.Dependency-Reverse": "Predecessor",
    "Microsoft.VSTS.Common.Affects-Forward": "Affects",
    "Microsoft.VSTS.Common.Affects-Reverse": "Affected By",
    "System.LinkTypes.Duplicate-Forward": "Duplicate",
    "System.LinkTypes.Duplicate-Reverse": "Duplicate Of",
    "Microsoft.VSTS.TestCase.SharedParameterReferencedBy-Forward": "Test Case",
    "Microsoft.VSTS.TestCase.SharedParameterReferencedBy-Reverse": "Shared Parameters",
}


def _normalize_relation(rel: dict) -> dict:
    """Convert raw ADO relation to an LLM-friendly format.

    Raw ADO returns: {"rel": "System.LinkTypes.Hierarchy-Forward",
    "url": ".../workItems/135", "attributes": {"name": "Child"}}

    Normalized: {"type": "Child", "target_id": 135, "rel": "System.LinkTypes.Hierarchy-Forward"}
    """
    rel_type = rel.get("rel", "")
    attrs = rel.get("attributes", {})
    url = rel.get("url", "")

    # Extract target work item ID from the URL
    target_id = None
    if "/workItems/" in url:
        try:
            target_id = int(url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            pass

    # Resolve human-readable type: prefer attributes.name, fall back to map, then raw rel
    type_name = attrs.get("name") or _RELATION_TYPE_MAP.get(rel_type, rel_type)

    return {
        "type": type_name,
        "target_id": target_id,
        "rel": rel_type,
    }


async def _fetch_work_item_details(
    client: ADOClient,
    project: str,
    ids: list[int],
    fields: list[str] | None = None,
) -> list[dict]:
    """Fetch work item details in batches of 200.

    Args:
        client: ADOClient instance.
        project: ADO project name.
        ids: Work item IDs to fetch.
        fields: Field reference names to include. Defaults to _DEFAULT_FIELDS.

    Returns:
        List of normalized work item dicts.
    """
    if not ids:
        return []

    field_list = fields if fields is not None else _DEFAULT_FIELDS
    fields_str = ",".join(field_list)
    all_items: list[dict] = []
    batch_size = 200

    for i in range(0, len(ids), batch_size):
        batch = ids[i : i + batch_size]
        ids_str = ",".join(str(id_) for id_ in batch)
        result = await client.get(
            f"/{project}/_apis/wit/workitems",
            params={"ids": ids_str, "fields": fields_str},
        )
        for item in result.get("value", []):
            all_items.append(_normalize_work_item(item, fields))

    return all_items


def _count_working_days(start_str: str, end_str: str) -> int:
    """Count weekday working days between two ISO date strings (inclusive)."""
    if not start_str or not end_str:
        return 10  # Default 2-week sprint assumption

    try:
        start = datetime.date.fromisoformat(start_str[:10])
        end = datetime.date.fromisoformat(end_str[:10])
    except ValueError:
        return 10

    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += datetime.timedelta(days=1)
    return count


def _parse_fields(fields: str | None) -> list[str] | None:
    """Parse comma-separated fields string into a list, or None for defaults."""
    if not fields:
        return None
    return [f.strip() for f in fields.split(",") if f.strip()]


def register_work_item_tools(mcp: FastMCP) -> None:
    """Register all work item read tools with the MCP server."""

    @mcp.tool()
    @handle_tool_errors
    async def wit_query_wiql(
        ctx: Context,
        project: str,
        wiql: str,
        fetch_details: bool = False,
        fields: str | None = None,
    ) -> str:
        """Execute a WIQL query against Azure DevOps work items.

        Returns matching work item IDs. Set fetch_details=True to also return
        full field data. Use the fields parameter (comma-separated reference names)
        to request specific fields including custom ones like Custom.TShirtSize.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.post(
                f"/{project}/_apis/wit/wiql",
                json={"query": wiql},
            )

            item_refs = result.get("workItems", [])

            if not item_refs:
                return json.dumps({"count": 0, "work_items": []}, indent=2)

            if not fetch_details:
                items = [{"id": ref["id"]} for ref in item_refs]
                return json.dumps({"count": len(items), "work_items": items}, indent=2)

            ids = [ref["id"] for ref in item_refs]
            parsed_fields = _parse_fields(fields)
            items = await _fetch_work_item_details(client, project, ids, parsed_fields)
            return json.dumps({"count": len(items), "work_items": items}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_get_work_item(
        ctx: Context,
        project: str,
        work_item_id: int,
        fields: str | None = None,
    ) -> str:
        """Get a single work item by ID with relations (parent/child links).

        Use the fields parameter (comma-separated reference names) to request
        specific fields including custom ones.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            parsed_fields = _parse_fields(fields)
            params: dict = {"$expand": "relations"}
            if parsed_fields:
                params["fields"] = ",".join(parsed_fields)
            result = await client.get(
                f"/{project}/_apis/wit/workitems/{work_item_id}",
                params=params,
            )

            item = _normalize_work_item(result, parsed_fields)

            # Include relations if present — normalized for LLM consumption
            relations = result.get("relations", [])
            if relations:
                item["relations"] = [
                    _normalize_relation(r) for r in relations
                ]

            return json.dumps(item, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_get_work_items_batch(
        ctx: Context,
        project: str,
        ids: str,
        fields: str | None = None,
    ) -> str:
        """Get multiple work items by IDs (comma-separated). Batches at 200.

        Use the fields parameter (comma-separated reference names) to request
        specific fields including custom ones.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
            parsed_fields = _parse_fields(fields)
            items = await _fetch_work_item_details(client, project, id_list, parsed_fields)
            return json.dumps({"count": len(items), "work_items": items}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_get_work_items_for_iteration(
        ctx: Context,
        project: str,
        iteration_path: str,
    ) -> str:
        """Get all work items for a specific iteration (sprint) path.

        Convenience wrapper that runs a WIQL query filtered by iteration path
        and fetches full details.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            safe_iteration_path = iteration_path.replace("'", "''")
            wiql = (
                "SELECT [System.Id] FROM WorkItems "
                f"WHERE [System.IterationPath] = '{safe_iteration_path}' "
                "ORDER BY [System.Id]"
            )
            result = await client.post(
                f"/{project}/_apis/wit/wiql",
                json={"query": wiql},
            )

            item_refs = result.get("workItems", [])

            if not item_refs:
                return json.dumps({"count": 0, "work_items": []}, indent=2)

            ids = [ref["id"] for ref in item_refs]
            items = await _fetch_work_item_details(client, project, ids)
            return json.dumps({"count": len(items), "work_items": items}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_my_work_items(ctx: Context) -> str:
        """Get work items assigned to the current user that are not Closed.

        Uses @Me WIQL macro — no project scope needed. Returns full details.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            wiql = (
                "SELECT [System.Id] FROM WorkItems "
                "WHERE [System.AssignedTo] = @Me "
                "AND [System.State] <> 'Closed' "
                "ORDER BY [System.ChangedDate] DESC"
            )
            result = await client.post(
                "/_apis/wit/wiql",
                json={"query": wiql},
            )
            item_refs = result.get("workItems", [])

            if not item_refs:
                return json.dumps({"count": 0, "work_items": []}, indent=2)

            # @Me queries can span projects — fetch without project scope
            # We need at least one project context for the batch fetch;
            # use the org-level endpoint instead
            ids = [ref["id"] for ref in item_refs]
            ids_str = ",".join(str(id_) for id_ in ids[:200])
            field_list = _DEFAULT_FIELDS
            fields_str = ",".join(field_list)
            batch_result = await client.get(
                "/_apis/wit/workitems",
                params={"ids": ids_str, "fields": fields_str},
            )
            items = [_normalize_work_item(item, None) for item in batch_result.get("value", [])]
            return json.dumps({"count": len(items), "work_items": items}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_list_backlogs(
        ctx: Context,
        project: str,
        team: str,
    ) -> str:
        """List backlog levels (Epics, Features, Stories, etc.) for a team."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(f"/{project}/{team}/_apis/work/backlogs")
            backlogs = [
                {
                    "id": b.get("id", ""),
                    "name": b.get("name", ""),
                    "rank": b.get("rank", 0),
                    "work_item_types": [
                        t.get("name", "") for t in b.get("workItemTypes", [])
                    ],
                }
                for b in result.get("value", [])
            ]
            return json.dumps({"count": len(backlogs), "backlogs": backlogs}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_list_backlog_work_items(
        ctx: Context,
        project: str,
        team: str,
        backlog_id: str,
    ) -> str:
        """List work items in a specific backlog level for a team."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/{team}/_apis/work/backlogs/{backlog_id}/workItems"
            )
            items = [
                {
                    "target": {
                        "id": item.get("target", {}).get("id"),
                        "url": item.get("target", {}).get("url", ""),
                    },
                }
                for item in result.get("workItems", [])
            ]
            return json.dumps({"count": len(items), "work_items": items}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_get_work_item_type(
        ctx: Context,
        project: str,
        type_name: str,
    ) -> str:
        """Get field definitions, state transitions, and rules for a work item type.

        Works for both system types (User Story, Bug) and custom types.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/_apis/wit/workitemtypes/{type_name}"
            )
            wit = {
                "name": result.get("name", ""),
                "reference_name": result.get("referenceName", ""),
                "description": result.get("description", ""),
                "fields": [
                    {
                        "reference_name": f.get("referenceName", ""),
                        "name": f.get("name", ""),
                        "type": f.get("type", ""),
                        "always_required": f.get("alwaysRequired", False),
                        "default_value": f.get("defaultValue", ""),
                    }
                    for f in result.get("fieldInstances", result.get("fields", []))
                ],
                "states": [
                    {"name": s.get("name", ""), "color": s.get("color", ""), "category": s.get("category", "")}
                    for s in result.get("states", [])
                ],
                "transitions": result.get("transitions", {}),
            }
            return json.dumps(wit, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_list_fields(
        ctx: Context,
        project: str,
    ) -> str:
        """List all fields (system and custom) for a project.

        Returns reference names, display names, and types. Use this to discover
        custom fields like Custom.TShirtSize before querying work items.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(f"/{project}/_apis/wit/fields")
            fields = [
                {
                    "reference_name": f.get("referenceName", ""),
                    "name": f.get("name", ""),
                    "type": f.get("type", ""),
                    "is_identity": f.get("isIdentity", False),
                    "is_picklist": f.get("isPicklist", False),
                    "usage": f.get("usage", ""),
                }
                for f in result.get("value", [])
            ]
            return json.dumps({"count": len(fields), "fields": fields}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_get_team_iterations(
        ctx: Context,
        project: str,
        team: str,
        timeframe: str | None = None,
    ) -> str:
        """Get iteration (sprint) schedules for a team.

        Optional timeframe filter: 'past', 'current', or 'future'.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            params: dict = {}
            if timeframe:
                params["$timeframe"] = timeframe
            result = await client.get(
                f"/{project}/{team}/_apis/work/teamsettings/iterations",
                params=params,
            )
            iterations = []
            for entry in result.get("value", []):
                attrs = entry.get("attributes", {})
                iterations.append({
                    "id": entry.get("id", ""),
                    "name": entry.get("name", ""),
                    "path": entry.get("path", ""),
                    "start_date": attrs.get("startDate"),
                    "end_date": attrs.get("finishDate"),
                    "time_frame": attrs.get("timeFrame", ""),
                })
            return json.dumps({"count": len(iterations), "iterations": iterations}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_get_team_capacity(
        ctx: Context,
        project: str,
        team: str,
        iteration_id: str,
    ) -> str:
        """Get team member capacity for a sprint iteration.

        Returns per-member breakdown with total available hours calculated as:
        capacity_per_day * (working_days - days_off).
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            # Fetch capacity and iteration dates in sequence (same client)
            cap_result = await client.get(
                f"/{project}/{team}/_apis/work/teamsettings/iterations/{iteration_id}/capacities"
            )
            iter_result = await client.get(
                f"/{project}/{team}/_apis/work/teamsettings/iterations/{iteration_id}"
            )

            attrs = iter_result.get("attributes", {})
            start_date = attrs.get("startDate", "")
            end_date = attrs.get("finishDate", "")
            working_days = _count_working_days(start_date, end_date)

            capacities = []
            for entry in cap_result.get("value", []):
                member = entry.get("teamMember", {}).get("displayName", "Unknown")
                days_off = len(entry.get("daysOff", []))
                effective_days = max(0, working_days - days_off)

                for activity in entry.get("activities", []):
                    cap_per_day = activity.get("capacityPerDay", 0)
                    capacities.append({
                        "team_member": member,
                        "activity": activity.get("name", ""),
                        "capacity_per_day": cap_per_day,
                        "days_off": days_off,
                        "working_days": working_days,
                        "effective_days": effective_days,
                        "total_available_hours": cap_per_day * effective_days,
                    })

            return json.dumps(
                {
                    "iteration_id": iteration_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "working_days": working_days,
                    "count": len(capacities),
                    "capacities": capacities,
                },
                indent=2,
            )
        finally:
            await client.close()

    # --- Write tools ---

    @mcp.tool()
    @handle_tool_errors
    async def wit_create_work_item(
        ctx: Context,
        project: str,
        type_name: str,
        title: str,
        fields: dict | None = None,
        parent_id: int | None = None,
    ) -> str:
        """Create a new work item in an Azure DevOps project.

        Args:
            project: ADO project name.
            type_name: Work item type (e.g. "User Story", "Task", "Bug").
            title: Title for the work item.
            fields: Optional dict of additional field reference names to values,
                e.g. {"System.AssignedTo": "sam@example.com", "Microsoft.VSTS.Scheduling.StoryPoints": 5}.
            parent_id: Optional parent work item ID to create a hierarchy link.
        """
        org = get_org(ctx)
        client = ADOClient(org, get_token(ctx))
        try:
            ops: list[dict] = [{"op": "add", "path": "/fields/System.Title", "value": title}]

            if fields:
                ops.extend(_build_field_ops(fields))

            if parent_id is not None:
                ops.append({
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "System.LinkTypes.Hierarchy-Reverse",
                        "url": f"https://dev.azure.com/{org}/{project}/_apis/wit/workItems/{parent_id}",
                    },
                })

            result = await client.post_json_patch(
                f"/{project}/_apis/wit/workitems/${type_name}",
                json_patch=ops,
            )
            item = _normalize_work_item(result, None)
            return json.dumps(item, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_update_work_item(
        ctx: Context,
        project: str,
        work_item_id: int,
        fields: dict,
    ) -> str:
        """Update fields on an existing work item.

        Args:
            project: ADO project name.
            work_item_id: The work item ID to update.
            fields: Dict of field reference names to new values,
                e.g. {"System.State": "Active", "System.AssignedTo": "sam@example.com"}.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            ops = _build_field_ops(fields)
            result = await client.patch(
                f"/{project}/_apis/wit/workitems/{work_item_id}",
                json_patch=ops,
            )
            item = _normalize_work_item(result, None)
            return json.dumps(item, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_update_work_items_batch(
        ctx: Context,
        project: str,
        updates: list,
    ) -> str:
        """Update multiple work items in a single call.

        Args:
            project: ADO project name.
            updates: List of objects with "id" (int) and "fields" (dict),
                e.g. [{"id": 1, "fields": {"System.State": "Closed"}}, ...].
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            update_list = updates
            results: list[dict] = []
            for entry in update_list:
                wid = entry["id"]
                ops = _build_field_ops(entry["fields"])
                try:
                    result = await client.patch(
                        f"/{project}/_apis/wit/workitems/{wid}",
                        json_patch=ops,
                    )
                    title = result.get("fields", {}).get("System.Title", "")
                    results.append({"id": wid, "success": True, "title": title})
                except httpx.HTTPStatusError as e:
                    results.append({
                        "id": wid, "success": False,
                        "status": e.response.status_code, "message": e.response.text,
                    })
            succeeded = sum(1 for r in results if r["success"])
            failed = len(results) - succeeded
            return json.dumps({
                "total": len(results), "succeeded": succeeded,
                "failed": failed, "results": results,
            }, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_add_work_item_comment(
        ctx: Context,
        project: str,
        work_item_id: int,
        text: str,
    ) -> str:
        """Add a comment to a work item.

        Args:
            project: ADO project name.
            work_item_id: The work item ID.
            text: Comment text (supports HTML).
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.post(
                f"/{project}/_apis/wit/workitems/{work_item_id}/comments",
                json={"text": text},
                params={"api-version": "7.1-preview.4"},
            )
            return json.dumps({
                "id": result.get("id"),
                "work_item_id": work_item_id,
                "text": result.get("text", ""),
                "created_by": result.get("createdBy", {}).get("displayName", ""),
                "created_date": result.get("createdDate", ""),
            }, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_add_child_work_items(
        ctx: Context,
        project: str,
        parent_id: int,
        child_ids: str,
    ) -> str:
        """Add child links to a parent work item.

        Args:
            project: ADO project name.
            parent_id: The parent work item ID.
            child_ids: Comma-separated child work item IDs, e.g. "10,11,12".
        """
        org = get_org(ctx)
        client = ADOClient(org, get_token(ctx))
        try:
            ids = [int(x.strip()) for x in child_ids.split(",") if x.strip()]
            ops = [
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "System.LinkTypes.Hierarchy-Forward",
                        "url": f"https://dev.azure.com/{org}/{project}/_apis/wit/workItems/{cid}",
                    },
                }
                for cid in ids
            ]
            result = await client.patch(
                f"/{project}/_apis/wit/workitems/{parent_id}",
                json_patch=ops,
            )
            item = _normalize_work_item(result, None)
            return json.dumps(item, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_work_items_link(
        ctx: Context,
        project: str,
        work_item_id: int,
        target_id: int,
        link_type: str = "System.LinkTypes.Related",
    ) -> str:
        """Add a link between two work items.

        Args:
            project: ADO project name.
            work_item_id: The source work item ID.
            target_id: The target work item ID to link to.
            link_type: Relation type reference name (default: System.LinkTypes.Related).
                Common types: System.LinkTypes.Hierarchy-Forward (child),
                System.LinkTypes.Hierarchy-Reverse (parent),
                System.LinkTypes.Related, System.LinkTypes.Dependency-Forward.
        """
        org = get_org(ctx)
        client = ADOClient(org, get_token(ctx))
        try:
            ops = [{
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": link_type,
                    "url": f"https://dev.azure.com/{org}/{project}/_apis/wit/workItems/{target_id}",
                },
            }]
            result = await client.patch(
                f"/{project}/_apis/wit/workitems/{work_item_id}",
                json_patch=ops,
            )
            item = _normalize_work_item(result, None)
            return json.dumps(item, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wit_work_item_unlink(
        ctx: Context,
        project: str,
        work_item_id: int,
        relation_index: int,
    ) -> str:
        """Remove a relation from a work item by index.

        Use wit_get_work_item first to see the relations array and determine the
        correct index to remove.

        Args:
            project: ADO project name.
            work_item_id: The work item ID.
            relation_index: Zero-based index of the relation to remove.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            ops = [{"op": "remove", "path": f"/relations/{relation_index}"}]
            result = await client.patch(
                f"/{project}/_apis/wit/workitems/{work_item_id}",
                json_patch=ops,
            )
            item = _normalize_work_item(result, None)
            return json.dumps(item, indent=2)
        finally:
            await client.close()
