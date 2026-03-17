"""Wiki tools: list wikis, read/write pages."""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP

from ado_mcp_server.utils.ado_client import ADOClient
from ado_mcp_server.utils.auth import get_org, get_token
from ado_mcp_server.utils.tool_handler import handle_tool_errors


def _flatten_pages(page: dict, depth: int = 0) -> list[dict]:
    """Recursively flatten a wiki page tree into a flat list with depth."""
    result = [
        {
            "path": page.get("path", ""),
            "id": page.get("id"),
            "depth": depth,
            "order": page.get("order"),
            "isParentPage": page.get("isParentPage", False),
        }
    ]
    for child in page.get("subPages", []):
        result.extend(_flatten_pages(child, depth + 1))
    return result


def register_wiki_tools(mcp: FastMCP) -> None:
    """Register all wiki tools with the MCP server."""

    @mcp.tool()
    @handle_tool_errors
    async def wiki_list_wikis(
        ctx: Context,
        project: str,
    ) -> str:
        """List all wikis in an Azure DevOps project."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(f"/{project}/_apis/wiki/wikis")
            wikis = [
                {
                    "id": w["id"],
                    "name": w["name"],
                    "type": w.get("type", ""),
                    "url": w.get("url", ""),
                }
                for w in result.get("value", [])
            ]
            return json.dumps({"count": len(wikis), "wikis": wikis}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wiki_get_wiki(
        ctx: Context,
        project: str,
        wiki_id: str,
    ) -> str:
        """Get details of a specific wiki by name or ID."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(f"/{project}/_apis/wiki/wikis/{wiki_id}")
            return json.dumps(
                {
                    "id": result["id"],
                    "name": result["name"],
                    "type": result.get("type", ""),
                    "url": result.get("url", ""),
                    "versions": [
                        {"version": v.get("version", ""), "versionType": v.get("versionType", "")}
                        for v in result.get("versions", [])
                    ],
                    "repositoryId": result.get("repositoryId", ""),
                    "mappedPath": result.get("mappedPath", ""),
                },
                indent=2,
            )
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wiki_create_wiki(
        ctx: Context,
        project: str,
        name: str,
        type: str = "projectWiki",
    ) -> str:
        """Create a new wiki in an Azure DevOps project.

        Args:
            name: Wiki name (e.g. 'Cast.wiki').
            type: Wiki type — 'projectWiki' (managed, default) or 'codeWiki' (backed by a Git repo).
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            # Get the project ID for the request body
            project_info = await client.get(f"/_apis/projects/{project}")
            body: dict = {
                "name": name,
                "type": type,
                "projectId": project_info["id"],
            }
            result = await client.post(f"/{project}/_apis/wiki/wikis", json=body)
            return json.dumps(
                {
                    "id": result["id"],
                    "name": result["name"],
                    "type": result.get("type", ""),
                    "url": result.get("url", ""),
                },
                indent=2,
            )
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wiki_list_pages(
        ctx: Context,
        project: str,
        wiki_id: str,
    ) -> str:
        """List all pages in a wiki as a flat list with depth.

        Returns the full page tree flattened, with each page's path and nesting depth.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/_apis/wiki/wikis/{wiki_id}/pages",
                params={"recursionLevel": "full"},
            )
            pages = _flatten_pages(result)
            return json.dumps({"count": len(pages), "pages": pages}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wiki_get_page_content(
        ctx: Context,
        project: str,
        wiki_id: str,
        path: str,
    ) -> str:
        """Get a wiki page's markdown content and ETag.

        The ETag is needed for updating the page — pass it to wiki_create_or_update_page.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            # Use raw httpx client to access response headers (for ETag).
            raw_client = await client._get_client()
            params = {"path": path, "includeContent": "true", "api-version": client.API_VERSION}
            raw_resp = await raw_client.get(f"/{project}/_apis/wiki/wikis/{wiki_id}/pages", params=params)
            raw_resp.raise_for_status()
            data = raw_resp.json()
            etag = raw_resp.headers.get("ETag", "")

            return json.dumps(
                {
                    "path": data.get("path", ""),
                    "content": data.get("content", ""),
                    "gitItemPath": data.get("gitItemPath", ""),
                    "etag": etag,
                },
                indent=2,
            )
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def wiki_create_or_update_page(
        ctx: Context,
        project: str,
        wiki_id: str,
        path: str,
        content: str,
        etag: str | None = None,
    ) -> str:
        """Create or update a wiki page.

        Args:
            path: Wiki page path (e.g. '/Getting-Started' or '/Specs/My-Page').
            content: Markdown content for the page.
            etag: ETag from wiki_get_page_content. Required for updates, omit for new pages.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            extra_headers: dict[str, str] = {}
            if etag:
                extra_headers["If-Match"] = etag

            resp = await client.put(
                f"/{project}/_apis/wiki/wikis/{wiki_id}/pages",
                json={"content": content},
                params={"path": path},
                extra_headers=extra_headers,
            )
            data = resp.json()
            return json.dumps(
                {
                    "path": data.get("path", ""),
                    "gitItemPath": data.get("gitItemPath", ""),
                    "eTag": resp.headers.get("ETag", ""),
                },
                indent=2,
            )
        finally:
            await client.close()
