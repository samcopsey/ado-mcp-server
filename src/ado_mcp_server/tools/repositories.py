"""Repository tools: repos, branches, commits, pull requests, PR threads."""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP

from ado_mcp_server.utils.ado_client import ADOClient
from ado_mcp_server.utils.auth import get_org, get_token
from ado_mcp_server.utils.tool_handler import handle_tool_errors


def register_repository_tools(mcp: FastMCP) -> None:
    """Register all repository tools with the MCP server."""

    # --- Repositories ---

    @mcp.tool()
    @handle_tool_errors
    async def repo_list_repos(
        ctx: Context,
        project: str,
    ) -> str:
        """List all Git repositories in an Azure DevOps project."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(f"/{project}/_apis/git/repositories")
            repos = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "defaultBranch": r.get("defaultBranch", ""),
                    "webUrl": r.get("webUrl", ""),
                    "size": r.get("size", 0),
                }
                for r in result.get("value", [])
            ]
            return json.dumps({"count": len(repos), "repositories": repos}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def repo_get_repo(
        ctx: Context,
        project: str,
        repository: str,
    ) -> str:
        """Get details of a specific Git repository by name or ID."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(f"/{project}/_apis/git/repositories/{repository}")
            return json.dumps(
                {
                    "id": result["id"],
                    "name": result["name"],
                    "defaultBranch": result.get("defaultBranch", ""),
                    "webUrl": result.get("webUrl", ""),
                    "size": result.get("size", 0),
                    "remoteUrl": result.get("remoteUrl", ""),
                    "sshUrl": result.get("sshUrl", ""),
                },
                indent=2,
            )
        finally:
            await client.close()

    # --- Branches ---

    @mcp.tool()
    @handle_tool_errors
    async def repo_list_branches(
        ctx: Context,
        project: str,
        repository: str,
    ) -> str:
        """List all branches in a Git repository."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/_apis/git/repositories/{repository}/refs",
                params={"filter": "heads/"},
            )
            branches = [
                {
                    "name": ref["name"].removeprefix("refs/heads/"),
                    "objectId": ref["objectId"],
                }
                for ref in result.get("value", [])
            ]
            return json.dumps({"count": len(branches), "branches": branches}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def repo_create_branch(
        ctx: Context,
        project: str,
        repository: str,
        name: str,
        source_branch: str = "main",
    ) -> str:
        """Create a new branch in a Git repository.

        First resolves the source branch HEAD commit, then creates the new branch ref.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            # Resolve source branch objectId
            refs = await client.get(
                f"/{project}/_apis/git/repositories/{repository}/refs",
                params={"filter": f"heads/{source_branch}"},
            )
            ref_list = refs.get("value", [])
            if not ref_list:
                return json.dumps({"error": True, "message": f"Source branch '{source_branch}' not found"}, indent=2)
            source_object_id = ref_list[0]["objectId"]

            # Create the new branch ref
            result = await client.post(
                f"/{project}/_apis/git/repositories/{repository}/refs",
                json=[
                    {
                        "name": f"refs/heads/{name}",
                        "oldObjectId": "0000000000000000000000000000000000000000",
                        "newObjectId": source_object_id,
                    }
                ],
            )
            created = result.get("value", [{}])[0]
            if not created.get("success", True):
                return json.dumps(
                    {"error": True, "message": created.get("customMessage", "Branch creation failed")}, indent=2
                )
            return json.dumps(
                {
                    "name": name,
                    "objectId": source_object_id,
                    "success": True,
                },
                indent=2,
            )
        finally:
            await client.close()

    # --- Commits ---

    @mcp.tool()
    @handle_tool_errors
    async def repo_search_commits(
        ctx: Context,
        project: str,
        repository: str,
        author: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        item_path: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> str:
        """Search commits in a Git repository with optional filters.

        Args:
            author: Filter by author name or email.
            from_date: Start date (ISO 8601, e.g. '2026-01-01').
            to_date: End date (ISO 8601, e.g. '2026-03-01').
            item_path: Filter by file path (e.g. '/src/main.py').
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            params: dict = {"$top": top, "$skip": skip}
            if author:
                params["searchCriteria.author"] = author
            if from_date:
                params["searchCriteria.fromDate"] = from_date
            if to_date:
                params["searchCriteria.toDate"] = to_date
            if item_path:
                params["searchCriteria.itemPath"] = item_path
            result = await client.get(
                f"/{project}/_apis/git/repositories/{repository}/commits",
                params=params,
            )
            commits = [
                {
                    "commitId": c["commitId"][:12],
                    "comment": c.get("comment", ""),
                    "author": c.get("author", {}).get("name", ""),
                    "date": c.get("author", {}).get("date", ""),
                }
                for c in result.get("value", [])
            ]
            return json.dumps({"count": len(commits), "commits": commits}, indent=2)
        finally:
            await client.close()

    # --- Pull Requests ---

    @mcp.tool()
    @handle_tool_errors
    async def repo_list_pull_requests(
        ctx: Context,
        project: str,
        repository: str | None = None,
        status: str = "active",
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref: str | None = None,
        target_ref: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> str:
        """List pull requests in a project or specific repository.

        Args:
            status: Filter by status: 'active', 'completed', 'abandoned', 'all'.
            source_ref: Filter by source branch name (e.g. 'refs/heads/feature').
            target_ref: Filter by target branch name (e.g. 'refs/heads/main').
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            params: dict = {
                "searchCriteria.status": status,
                "$top": top,
                "$skip": skip,
            }
            if creator_id:
                params["searchCriteria.creatorId"] = creator_id
            if reviewer_id:
                params["searchCriteria.reviewerId"] = reviewer_id
            if source_ref:
                params["searchCriteria.sourceRefName"] = source_ref
            if target_ref:
                params["searchCriteria.targetRefName"] = target_ref

            if repository:
                path = f"/{project}/_apis/git/repositories/{repository}/pullrequests"
            else:
                path = f"/{project}/_apis/git/pullrequests"

            result = await client.get(path, params=params)
            prs = [
                {
                    "pullRequestId": pr["pullRequestId"],
                    "title": pr.get("title", ""),
                    "status": pr.get("status", ""),
                    "createdBy": pr.get("createdBy", {}).get("displayName", ""),
                    "sourceRefName": pr.get("sourceRefName", ""),
                    "targetRefName": pr.get("targetRefName", ""),
                    "creationDate": pr.get("creationDate", ""),
                }
                for pr in result.get("value", [])
            ]
            return json.dumps({"count": len(prs), "pull_requests": prs}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def repo_get_pull_request(
        ctx: Context,
        project: str,
        repository: str,
        pull_request_id: int,
    ) -> str:
        """Get details of a specific pull request."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/_apis/git/repositories/{repository}/pullrequests/{pull_request_id}"
            )
            reviewers = [
                {
                    "displayName": r.get("displayName", ""),
                    "vote": r.get("vote", 0),
                    "id": r.get("id", ""),
                }
                for r in result.get("reviewers", [])
            ]
            return json.dumps(
                {
                    "pullRequestId": result["pullRequestId"],
                    "title": result.get("title", ""),
                    "description": result.get("description", ""),
                    "status": result.get("status", ""),
                    "createdBy": result.get("createdBy", {}).get("displayName", ""),
                    "sourceRefName": result.get("sourceRefName", ""),
                    "targetRefName": result.get("targetRefName", ""),
                    "mergeStatus": result.get("mergeStatus", ""),
                    "creationDate": result.get("creationDate", ""),
                    "closedDate": result.get("closedDate", ""),
                    "reviewers": reviewers,
                    "isDraft": result.get("isDraft", False),
                },
                indent=2,
            )
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def repo_create_pull_request(
        ctx: Context,
        project: str,
        repository: str,
        source_ref: str,
        target_ref: str,
        title: str,
        description: str = "",
        is_draft: bool = False,
    ) -> str:
        """Create a pull request in a Git repository.

        Args:
            source_ref: Source branch name (e.g. 'feature-branch' or 'refs/heads/feature-branch').
            target_ref: Target branch name (e.g. 'main' or 'refs/heads/main').
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            # Ensure refs/heads/ prefix
            if not source_ref.startswith("refs/"):
                source_ref = f"refs/heads/{source_ref}"
            if not target_ref.startswith("refs/"):
                target_ref = f"refs/heads/{target_ref}"

            body = {
                "sourceRefName": source_ref,
                "targetRefName": target_ref,
                "title": title,
                "description": description,
                "isDraft": is_draft,
            }
            result = await client.post(
                f"/{project}/_apis/git/repositories/{repository}/pullrequests",
                json=body,
            )
            return json.dumps(
                {
                    "pullRequestId": result["pullRequestId"],
                    "title": result.get("title", ""),
                    "status": result.get("status", ""),
                    "sourceRefName": result.get("sourceRefName", ""),
                    "targetRefName": result.get("targetRefName", ""),
                    "url": result.get("url", ""),
                },
                indent=2,
            )
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def repo_update_pull_request(
        ctx: Context,
        project: str,
        repository: str,
        pull_request_id: int,
        status: str | None = None,
        title: str | None = None,
        description: str | None = None,
        is_draft: bool | None = None,
    ) -> str:
        """Update a pull request (change status, title, description, or draft state).

        Args:
            status: Set status: 'active', 'completed' (merge), 'abandoned'.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            body: dict = {}
            if status is not None:
                body["status"] = status
            if title is not None:
                body["title"] = title
            if description is not None:
                body["description"] = description
            if is_draft is not None:
                body["isDraft"] = is_draft

            result = await client.patch_json(
                f"/{project}/_apis/git/repositories/{repository}/pullrequests/{pull_request_id}",
                json=body,
            )
            return json.dumps(
                {
                    "pullRequestId": result["pullRequestId"],
                    "title": result.get("title", ""),
                    "status": result.get("status", ""),
                    "mergeStatus": result.get("mergeStatus", ""),
                },
                indent=2,
            )
        finally:
            await client.close()

    # --- PR Threads & Comments ---

    @mcp.tool()
    @handle_tool_errors
    async def repo_list_pr_threads(
        ctx: Context,
        project: str,
        repository: str,
        pull_request_id: int,
    ) -> str:
        """List all comment threads on a pull request."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(
                f"/{project}/_apis/git/repositories/{repository}/pullrequests/{pull_request_id}/threads"
            )
            threads = [
                {
                    "id": t["id"],
                    "status": t.get("status", ""),
                    "comments": [
                        {
                            "id": c["id"],
                            "author": c.get("author", {}).get("displayName", ""),
                            "content": c.get("content", ""),
                        }
                        for c in t.get("comments", [])
                    ],
                    "threadContext": {
                        "filePath": t.get("threadContext", {}).get("filePath", "") if t.get("threadContext") else "",
                    },
                }
                for t in result.get("value", [])
            ]
            return json.dumps({"count": len(threads), "threads": threads}, indent=2)
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def repo_create_pr_thread(
        ctx: Context,
        project: str,
        repository: str,
        pull_request_id: int,
        content: str,
        status: str = "active",
        file_path: str | None = None,
    ) -> str:
        """Create a comment thread on a pull request.

        Args:
            content: The comment text.
            status: Thread status: 'active', 'fixed', 'wontFix', 'closed', 'byDesign', 'pending'.
            file_path: Optional file path to attach the thread to a specific file.
        """
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            body: dict = {
                "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
                "status": status,
            }
            if file_path:
                body["threadContext"] = {"filePath": file_path}

            result = await client.post(
                f"/{project}/_apis/git/repositories/{repository}/pullrequests/{pull_request_id}/threads",
                json=body,
            )
            return json.dumps(
                {
                    "id": result["id"],
                    "status": result.get("status", ""),
                },
                indent=2,
            )
        finally:
            await client.close()

    @mcp.tool()
    @handle_tool_errors
    async def repo_reply_to_comment(
        ctx: Context,
        project: str,
        repository: str,
        pull_request_id: int,
        thread_id: int,
        content: str,
    ) -> str:
        """Reply to a comment thread on a pull request."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.post(
                f"/{project}/_apis/git/repositories/{repository}/pullrequests/{pull_request_id}/threads/{thread_id}/comments",
                json={"content": content, "parentCommentId": 0, "commentType": 1},
            )
            return json.dumps(
                {
                    "id": result["id"],
                    "content": result.get("content", ""),
                    "author": result.get("author", {}).get("displayName", ""),
                },
                indent=2,
            )
        finally:
            await client.close()
